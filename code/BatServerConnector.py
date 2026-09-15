import requests
import json

"""
    Name of the animal is the ID used by anyone outside BatScanner World.
    It should be unique and be a one to one relation to the unique BatID ID
"""
class BatServerConnector:
    def __init__(self, port):
        self.local_server = f"https://127.0.0.1:{port}"

    # Add a new bat with a specied_id
    def newAnimal(self):
        response = requests.post(f"{self.local_server}/api/animals/new", verify=False)
        if response.status_code != 200:
            print(response.status_code, response.text)
            return

        return json.loads(response.text)

    # Get the last animal from server from DB
    def lastAnimal(self):
        response = requests.get(f"{self.local_server}/api/animals/last", verify=False)
        if response.status_code != 200:
            print(response.status_code, response.text)
            return

        return json.loads(response.text)

    # Edit a bat
    def changeAnimal(self, id, foreign_id, name, description, species_id, recapture, maintenanceData):
        data = {
            "id": id,
            "foreign_id": foreign_id,
            "qr_id": foreign_id,
            "name": name,
            "description": description,
            "species_id": species_id,
            "recapture": recapture,
            "maintenance_data": maintenanceData
        }
        response = requests.post(f"{self.local_server}/api/animals/edit", data=data, verify=False)
        if response.status_code != 200:
            print(response.status_code, response.text)
            return

        return json.loads(response.text)

    # Get a list of all saved species
    def getSpecies(self):
        response = requests.get(f"{self.local_server}/api/species", verify=False)
        if response.status_code != 200:
            print(response.status_code, response.text)
            return

        return json.loads(response.text)
    
    # Get a list of all saved animals
    def getAnimals(self):
        response = requests.get(f"{self.local_server}/api/animals", verify=False)
        if response.status_code != 200:
            print(response.status_code, response.text)
            return

        return json.loads(response.text)
