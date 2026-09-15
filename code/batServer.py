import os
import argparse
import pandas as pd

import datetime
from PIL import Image

from flask import Flask, request, render_template, jsonify, flash, redirect, url_for, send_from_directory
from flask_sqlalchemy import SQLAlchemy
import shutil
import threading
import urllib3
import yaml

from PyQt5.QtCore import QObject, pyqtSignal

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = "Bruce Wayne is Batman"

with open('config.yaml') as f:
    config = yaml.safe_load(f)

# DB Stuff
os.makedirs(f"{app.root_path}/static/capture/{config['machine']['name']}/", exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{app.root_path}/static/capture/{config['machine']['name']}/batcave_{config['machine']['name']}.sqlite"
app.config['machinename'] = config['machine']['name']
db = SQLAlchemy(app)


# --------------------------------------------------------------------------------------
# Classes
# --------------------------------------------------------------------------------------
class Species(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200))
    animals = db.relationship("Animal", backref='species', lazy=True)

    def __lt__(self, other):
        return self.name < other.name

    def __eq__(self, other):
        return self.name == other.name

    def __json__(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
        }


class Animal(db.Model):
    # from DB always unique
    id = db.Column(db.Integer, primary_key=True)
    # In case the Smithonians use a different ID
    foreign_id = db.Column(db.String(50))
    # from the QR code
    qr_id = db.Column(db.String(50))
    # For interesting individuals
    name = db.Column(db.String(50))
    # timestamp
    created = db.Column(db.DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    # in case something is important
    description = db.Column(db.String(200))
    # which bat species
    species_id = db.Column(db.Integer, db.ForeignKey('species.id'), nullable=True)
    recapture = db.Column(db.Boolean, nullable=False, default=0)
    maintenance_data = db.Column(db.Boolean, nullable=False, default=0)
    recapture_foreign_id = db.Column(db.Integer)
    location = db.Column(db.String(50))

    def __lt__(self, other):
        return self.id < other.id

    def __eq__(self, other):
        return self.id == other.id
    
    def __json__(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "foreign_id": self.foreign_id,
            "qr_id": self.qr_id,
            "species_id": self.species_id,
            "recapture": self.recapture,
            "maintenance_data": self.maintenance_data
        }


# --------------------------------------------------------------------------------------
@app.route('/')
def home():
    # Render the template with the title parameter
    return render_template('index.html', title='Batscanner')


# --------------------------------------------------------------------------------------
# Species
# --------------------------------------------------------------------------------------
@app.route('/species')
def species():
    species = sorted(Species.query.all())
    return render_template('species.html', species=species)

# --------------------------------------------------------------------------------------
# Animals
# --------------------------------------------------------------------------------------
@app.route('/animals')
def animals():
    animals = sorted(Animal.query.all())
    return render_template('animals.html', animals=animals)


@app.route('/animals/add', methods=['GET'])
def add_animals_form():
    species = sorted(Species.query.all())
    return render_template('animals_add.html', species=species)


@app.route('/animals/add', methods=['POST'])
def add_animals():
    name = request.form['name']
    species_id = request.form['species_id']
    description = request.form['description']

    if not name:
        flash('Name is required.', 'danger')
        return redirect(url_for('add_animals_form'))

    new_animal = Animal(name=name, description=description, species_id=species_id)
    db.session.add(new_animal)
    db.session.commit()

    flash('Animal added successfully!', 'success')
    return redirect(url_for('animals'))


@app.route('/animals/qr', methods=['GET'])
def get_qr():
    return render_template('qr.html')

@app.route('/animals/qr/<string:qr_id>', methods=['GET'])
def redirect_animal_qr(qr_id):
    animal = Animal.query.filter_by(qr_id=qr_id).first()
    if animal is not None:
        return redirect(url_for('edit_animals_form', animal_id=animal.id))
    else:
        flash('No such animal', 'warning')
        return redirect(url_for('get_qr'))


@app.route('/animals/edit/<int:animal_id>', methods=['GET'])
def edit_animals_form(animal_id):
    animal = Animal.query.get_or_404(animal_id)
    species = sorted(Species.query.all())
    return render_template('animals_edit.html', animal=animal, species=species)


@app.route('/animals/edit/<int:animal_id>', methods=['POST'])
def edit_animals(animal_id):
    animal = Animal.query.get_or_404(animal_id)

    animal.species_id = request.form["species_id"]
    animal.foreign_id = request.form['foreign_id']
    animal.qr_id = request.form['foreign_id']
    animal.name = request.form['name']

    if request.form.get('recapture') and request.form['recapture'] == "on":
        animal.recapture =  True
    else:
        animal.recapture =  False

    if request.form.get('maintenance_data') and request.form['maintenance_data'] == "on":
        animal.maintenance_data =  True
    else:
        animal.maintenance_data =  False

    animal.description = request.form['description']
    db.session.commit()

    # Send the changes to the QT Window
    animal_last = Animal.query.order_by(Animal.id.desc()).first()
    if animal_last.id == animal_id:
        species = Species.query.get_or_404(animal.species_id)
        app.config["self"].animalChanged.emit(animal.__json__(), species.name)

    flash('Animal updated successfully!', 'success')
    return redirect(url_for('animals'))


@app.route('/animals/editlast', methods=['GET'])
def edit_animals_last_form():
    animal = Animal.query.order_by(Animal.id.desc()).first()
    species = sorted(Species.query.all())
    return render_template('animals_edit.html', animal=animal, animal_id=animal.id, species=species)


@app.route('/animals/lastimage', methods=['GET'])
def last_image():
    animal = Animal.query.order_by(Animal.id.desc()).first()

    image_folder = os.path.join(app.static_folder, 'capture', app.config["machinename"], str(animal.id))
    if not os.path.exists(image_folder):
        image = "nothing.png"
    else:
        image_files_large = [
            os.path.join('capture', app.config["machinename"], str(animal.id), file)
            for file in sorted(os.listdir(image_folder), reverse=True)
            if file.endswith('.png')
        ]
        image = image_files_large[0]

    return render_template('last_image.html', image=image)


@app.route('/animals/delete/<int:animal_id>', methods=['POST'])
def delete_animal(animal_id):
    animal = Animal.query.get_or_404(animal_id)
    db.session.delete(animal)
    db.session.commit()
    flash('Species deleted successfully!', 'success')
    return redirect(url_for('animals'))


@app.route('/animals/gallery/<int:animal_id>', methods=['GET'])
def gallery_animals(animal_id):
    animal = Animal.query.get_or_404(animal_id)

    image_folder = os.path.join(app.static_folder, 'capture', app.config["machinename"], str(animal_id))
    if not os.path.exists(image_folder):
        return render_template('animals_gallery.html', animal=animal, images=[], machine_name=app.config["machinename"])

    image_files = [
        os.path.join('capture', app.config["machinename"], str(animal_id), file) 
        for file in sorted(os.listdir(image_folder), reverse=True) 
        if file.endswith('.jpg') and file.startswith("small_")
    ]
    image_files_large = [
        os.path.join('capture', app.config["machinename"], str(animal_id), file) 
        for file in sorted(os.listdir(image_folder), reverse=True) 
        if file.endswith('.png')
    ]

    """
    # TODO 
    image_folder_single = os.path.join(app.static_folder, 'capture', app.config["machinename"], str(animal_id), "singleImages")
    if os.path.exists(image_folder_single):
        image_files_large_single = [
            os.path.join('capture', app.config["machinename"], str(animal_id), "singleImages", file)
            for file in sorted(os.listdir(image_folder_single), reverse=True)
            if file.endswith('.png')
        ]
        image_files.extend(image_files_large_single)
    """

    if len(image_files) != len(image_files_large):
        print("converting ...")
        path = os.path.join(app.static_folder, 'capture', app.config["machinename"], str(animal_id))
        for file in os.listdir(path):
            if file.endswith(".png") and file not in image_files_large:
                im = Image.open(os.path.join(path, file))
                im = im.resize((360, 270), Image.LANCZOS)
                rgb_im = im.convert('RGB')
                rgb_im.save(os.path.join(path, f"small_{file}.jpg"))

        image_files = [
            os.path.join('capture', app.config["machinename"], str(animal_id), file) 
            for file in sorted(os.listdir(image_folder), reverse=True) 
            if file.endswith('.jpg') and file.startswith("small_")
        ]

    species = Species.query.get(animal.species_id)

    return render_template('animals_gallery.html', animal=animal, species=species, images=image_files, machine_name=app.config["machinename"])


# --------------------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------------------
@app.route('/download', methods=['GET'])
def download():
    path = os.path.join(app.static_folder, 'capture', app.config["machinename"])
    
    fname = "all_"+datetime.datetime.today().strftime('%Y-%m-%d')
    shutil.make_archive(fname, 'zip', path)
    return send_from_directory(app.root_path, fname+".zip")


# --------------------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------------------
@app.route('/statistics')
def statistics():
    con = db.session.connection()
    df = pd.read_sql_query("SELECT a.id, foreign_id, a.name AS bag_name, s.name AS species_name, a.description FROM animal AS a LEFT JOIN species AS s ON a.species_id = s.id WHERE s.name NOT NULL AND s.name != ''", con)

    ds_species = df['species_name'].value_counts()
    frame_species = {"Individuals": ds_species}
    df_species = pd.DataFrame(frame_species)
    df_species.index.name = "species_name"

    species_list = df_species.index.array.tolist() 
    count_list = df_species['Individuals'].tolist()

    num_species = len(species_list)
    num_individuals = len(df.index)

    return render_template("statistics.html", num_species=num_species, num_individuals=num_individuals, species_list=species_list, count_list=count_list)


@app.route('/location', methods=['GET'])
def location():
    return render_template('location.html')

@app.route('/location', methods=['POST'])
def location_save():
    net_id = request.form["net_id"]
    gps = request.form["gps"]
    for k, v in request.form.items():
        print(k, v)
        if k.startswith("qr-") and v != "":
            animal = Animal.query.filter_by(qr_id=v).first()
            if animal:
                animal.location = f"{net_id}-{gps}"
    db.session.commit()

    flash('Location updated successfully!', 'success')
    return redirect(url_for('location'))



# --------------------------------------------------------------------------------------
# Api
# --------------------------------------------------------------------------------------
# Send all the species
@app.route('/api/species')
def api_species():
    jsonifyable = [i.__json__() for i in sorted(Species.query.all())]

    return jsonify(jsonifyable)

@app.route('/api/animals')
def api_animals():
    jsonifyable = [i.__json__() for i in sorted(Animal.query.all())]

    return jsonify(jsonifyable)

# Add a new bat
@app.route('/api/animals/new', methods=['POST'])
def api_new_animals():
    new_animal = Animal(name="", description="", foreign_id="")
    db.session.add(new_animal)
    db.session.commit()

    data = new_animal.__json__()
    return jsonify(data)


@app.route('/api/animals/last', methods=['GET'])
def api_animals_last():
    animal = Animal.query.order_by(Animal.id.desc()).first()

    # If no animal in DB create the first one
    if animal is None:
        animal = Animal(name="", description="", foreign_id="")
        db.session.add(animal)
        db.session.commit()

        data = animal.__json__()
        return jsonify(data)
    else:
        data = animal.__json__()
        return jsonify(data)

# Edit a bat
@app.route('/api/animals/edit', methods=['POST'])
def api_edit_animals():
    animal = Animal.query.get_or_404(request.form["id"])
    animal.name = request.form['name']
    animal.description = request.form['description']
    animal.foreign_id = request.form['foreign_id']
    animal.qr_id = request.form['foreign_id']
    animal.recapture = (request.form['recapture'] == 'True')
    animal.maintenance_data = (request.form['maintenance_data'] == 'True')

    if "species_id" in request.form.keys():
        animal.species_id = request.form['species_id']

    db.session.commit()

    data = animal.__json__()
    return jsonify(data)

# Start the flask app in a QT Thread
class ServerThread(QObject):
    animalChanged = pyqtSignal(dict, str)

    def __init__(self, machinename, port):
        super().__init__()
        app.config['machinename'] = machinename
        app.config['port'] = port
        app.config['self'] = self

        with app.app_context():
            db.create_all()

            # if the db was deleted, fill it
            if len(Species.query.all()) == 0:
                print("adding species from species_names.txt")
                new_species = Species(name="", description="")
                db.session.add(new_species)
                with open(os.path.join("species_names.txt"), "r") as f:
                    for line in f:
                        line = line.strip()
                        if line != "":
                            new_species = Species(name=line, description="")
                            db.session.add(new_species)

                db.session.commit()
    
    def start(self):
        threading.Thread(target=lambda: app.run(host="0.0.0.0", ssl_context=('localhost-cert.pem', 'localhost-key.pem'), port=int(app.config["port"]), debug=True, use_reloader=False), daemon=True).start()
        # threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(app.config["port"]), debug=True, use_reloader=False)).start()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--machinename')
    parser.add_argument('--port', default=5000)
    args = parser.parse_args()

    batServer = ServerThread(args.machinename, args.port)
    batServer.start()
