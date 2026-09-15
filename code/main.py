import argparse
from functools import partial
import cv2
import glob
import numpy as np
import os
from pathlib import Path
import shutil
import time
import yaml
import sys

# Camera
from genicam.gentl import TimeoutException
from harvesters.core import Harvester
from harvesters.util.pfnc import mono_location_formats, rgb_formats, bgr_formats, rgba_formats, bgra_formats

# UI
from PyQt5.QtWidgets import *
from PyQt5.QtMultimedia import *
from PyQt5.QtMultimediaWidgets import *
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, QTimer, QEvent, QObject, QThread, pyqtSignal, pyqtSlot
import qdarktheme

import utils
import camUtils
import hwUtils
import translation

from batServer import ServerThread

from BatServerConnector import BatServerConnector

with open('config.yaml') as f:
    config = yaml.safe_load(f)

if config["machine"]["pi"]:
    from gpiozero import Button

class StepperWorker(QObject):
    finished = pyqtSignal()

    def __init__(self, direction, elevation):
        super(StepperWorker, self).__init__(None)

        self.direction = direction
        self.elevation = elevation

    @pyqtSlot()
    def run(self):
        if self.direction == 0:
            hwUtils.forward(self.elevation)
        else:
            hwUtils.backwards(self.elevation)

        self.finished.emit()

class SaveWorker(QObject):
    imagesSaved = pyqtSignal(Path)

    def __init__(self, images):
        super(SaveWorker, self).__init__(None)

        self.images = images

    @pyqtSlot()
    def run(self):
        folderPath = None

        for path, image in self.images:
            folderPath = Path(path).parent
            os.makedirs(folderPath, exist_ok=True)
            cv2.imwrite(path, image)
        
        self.imagesSaved.emit(folderPath)

class CaptureWorker(QObject):
    updateFeed = pyqtSignal(QImage)
    imageCaptured = pyqtSignal(np.ndarray, QImage)

    def __init__(self, imageAcquirer, cam):
        super(CaptureWorker, self).__init__(None)

        self.imageAcquirer = imageAcquirer
        self.cam = cam

        self.trigger = False
        self.terminate = False

    @pyqtSlot()
    def captureImage(self):
        self.trigger = True

    @pyqtSlot()
    def stopCapturing(self):
        self.terminate = True

    @pyqtSlot()
    def run(self):
        if self.imageAcquirer is not None:
            while not self.terminate:
                try:
                    with self.imageAcquirer.fetch(timeout=1) as buffer:
                        payload = buffer.payload
                        component = payload.components[0]
                        width = component.width
                        height = component.height
                        dataFormat = component.data_format

                        ## This conversion is from the Harvesters documentation ##

                        # Reshape the image so that it can be drawn on the VisPy canvas:
                        if dataFormat in mono_location_formats:
                            frame = component.data.reshape(height, width)
                        else:
                            # The image requires you to reshape it to draw it on the
                            # canvas:
                            if dataFormat in rgb_formats or dataFormat in rgba_formats or dataFormat in bgr_formats or dataFormat in bgra_formats:
                                frame = component.data.reshape(height, width, int(component.num_components_per_pixel))  # Set of R, G, B, and Alpha
                                if dataFormat in bgr_formats:
                                    # Swap every R and B:
                                    frame = frame[:, :, ::-1]

                        frame = frame.copy()

                    h, w, ch = frame.shape
                    bytesPerLine = ch * w

                    convertToQtFormat = QImage(frame.data, w, h, bytesPerLine, QImage.Format_RGB888)
                    p = convertToQtFormat.scaled(utils.VIEWFINDER_WIDTH, utils.VIEWFINDER_HEIGHT, Qt.KeepAspectRatio)

                    if self.trigger:
                        self.imageCaptured.emit(frame, p)

                        self.trigger = False

                    self.updateFeed.emit(p)
                        
                except TimeoutException:
                    continue

        elif self.cam is not None:
            while not self.terminate:
                ret, frame = self.cam.read()

                if ret:              
                    rgbImage = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                    h, w, ch = rgbImage.shape
                    bytesPerLine = ch * w
                    convertToQtFormat = QImage(rgbImage.data, w, h, bytesPerLine, QImage.Format_RGB888)
                    p = convertToQtFormat.scaled(utils.VIEWFINDER_WIDTH, utils.VIEWFINDER_HEIGHT, Qt.KeepAspectRatio)
                    
                    if self.trigger:
                        self.imageCaptured.emit(frame, p)

                        self.trigger = False

                    self.updateFeed.emit(p)

class ControlGrid(QWidget):
    def __init__(self, language, parent=None):
        super(ControlGrid, self).__init__(parent)

        layout = QGridLayout()

        self.externalIndexLabel = QLabel()
        layout.addWidget(self.externalIndexLabel, 0, 0)

        self.externalIndexEdit = QLineEdit()
        self.externalIndexEdit.setMinimumHeight(30)
        self.externalIndexEdit.textEdited.connect(self.flagParamsModified)
        layout.addWidget(self.externalIndexEdit, 0, 1)

        self.bagLabel = QLabel()
        layout.addWidget(self.bagLabel, 1, 0)
        self.bag = QLineEdit()
        self.bag.setMinimumHeight(30)
        self.bag.textEdited.connect(self.flagParamsModified)
        layout.addWidget(self.bag, 1, 1)
        
        self.speciesLabel = QLabel()
        layout.addWidget(self.speciesLabel, 0, 2)
        self.species = QComboBox()
        self.species.setMinimumHeight(30)
        self.species.addItems(species_list)
        self.species.activated.connect(self.flagParamsModified)
        layout.addWidget(self.species, 0, 3)

        self.remarkLabel = QLabel()
        layout.addWidget(self.remarkLabel, 1, 2)
        self.remarks = QLineEdit()
        self.remarks.setMinimumHeight(30)
        self.remarks.textEdited.connect(self.flagParamsModified)
        layout.addWidget(self.remarks, 1, 3)

        self.recaptureBox = QCheckBox()
        self.recaptureBox.stateChanged.connect(self.flagParamsModified)
        layout.addWidget(self.recaptureBox, 0, 4)

        self.maintenanceBox = QCheckBox()
        self.maintenanceBox.stateChanged.connect(self.flagParamsModified)
        layout.addWidget(self.maintenanceBox, 1, 4)
        
        self.saveButton = QPushButton()
        self.saveButton.setMinimumHeight(60)
        self.saveButton.setMinimumWidth(120)
        self.saveButton.setEnabled(False)
        self.saveButton.clicked.connect(
            lambda: parent.save(
                self.remarks.text(), 
                self.externalIndexEdit.text(), 
                self.bag.text(), 
                self.species.currentText(),
                self.recaptureBox.isChecked(),
                self.maintenanceBox.isChecked()
            )
        )
        layout.addWidget(self.saveButton, 0, 5, 2, 1)

        self.next = QPushButton()
        self.next.setMinimumHeight(60)
        self.next.setMinimumWidth(120)
        self.next.clicked.connect(parent.nextAnimal)
        layout.addWidget(self.next, 0, 6, 2, 1)

        self.translate(language)

        self.setLayout(layout)
        self.layout = layout

    def flagParamsModified(self):
        self.saveButton.setEnabled(True)

    def clear(self):
        self.externalIndexEdit.clear()
        self.species.setCurrentIndex(0)
        self.bag.clear()
        self.remarks.clear()
        self.recaptureBox.setChecked(False)
        self.maintenanceBox.setChecked(False)
        self.saveButton.setEnabled(False)
        self.externalIndexEdit.setStyleSheet(None)

    def translate(self, language):
        self.externalIndexLabel.setText(translation.STRINGS["external_id"][language])
        self.speciesLabel.setText(translation.STRINGS["species"][language])
        self.bagLabel.setText(translation.STRINGS["bag"][language])
        self.remarkLabel.setText(translation.STRINGS["remarks"][language])
        self.recaptureBox.setText(translation.STRINGS["recapture"][language])
        self.maintenanceBox.setText(translation.STRINGS["maintenance_data"][language])
        self.saveButton.setText(translation.STRINGS["save"][language])
        self.next.setText(translation.STRINGS["next_animal"][language])

    def setAnimal(self, animal):
        self.externalIndexEdit.setText(animal["foreign_id"])
        self.bag.setText(animal["name"])
        self.remarks.setText(animal["description"])
        self.recaptureBox.setChecked(animal["recapture"])
        self.maintenanceBox.setChecked(animal["maintenance_data"])

        for x in species_dict.keys():
            if species_dict[x] == animal["species_id"]:
                self.species.setCurrentIndex(species_list.index(x))

class StatusGrid(QWidget):
    def __init__(self, language, currentAnimal, parent=None):
        super(StatusGrid, self).__init__(parent)

        layout = QGridLayout()

        self.idLabel = QLabel()
        biggerFont = self.idLabel.font()
        biggerFont.setPointSize(16)
        self.idLabel.setAlignment(Qt.AlignCenter)
        self.idLabel.setFont(biggerFont)
        layout.addWidget(self.idLabel, 0, 0)

        self.diskUsageLabel = QLabel()
        bigFont = self.diskUsageLabel.font()
        bigFont.setPointSize(16)
        self.diskUsageLabel.setAlignment(Qt.AlignCenter)
        self.diskUsageLabel.setFont(bigFont)
        layout.addWidget(self.diskUsageLabel, 0, 1)

        self.statusLabel = QLabel()
        bigFont = self.statusLabel.font()
        bigFont.setPointSize(15)
        self.statusLabel.setAlignment(Qt.AlignCenter)
        self.statusLabel.setFont(bigFont)
        layout.addWidget(self.statusLabel, 1, 0, 1, 2)

        self.setLayout(layout)
        self.layout = layout

        self.update(language, currentAnimal)

    def update(self, language, currentAnimal):
        diskUsage = shutil.disk_usage(os.path.join("static", "capture", config["machine"]["name"]))
        self.diskUsage = int((diskUsage.used/diskUsage.total)*100)
        self.diskUsageLabel.setText(translation.STRINGS['disk_usage'][language].format(int(diskUsage.used/1024/1024/1024), self.diskUsage))
        if self.diskUsage >= utils.STORAGE_WARNING_THRESHOLD:
            self.diskUsageLabel.setStyleSheet("background-color : darkred")
        else:
            self.diskUsageLabel.setStyleSheet(None)

        qrId = currentAnimal["qr_id"]
        self.idLabel.setText(f"{translation.STRINGS['qr_id'][language]}{qrId}")
        if qrId == None or len(qrId) == 0:
            self.idLabel.setStyleSheet("background-color : darkred")
        else:
            self.idLabel.setStyleSheet(None)

class CamControlGrid(QWidget):
    def __init__(self, language, imgIndex, parent=None):
        super(CamControlGrid, self).__init__(parent)

        layout = QGridLayout()

        self.triggerOnce = QPushButton()
        self.triggerOnce.setMinimumHeight(60)
        self.triggerOnce.setMinimumWidth(120)
        self.triggerOnce.clicked.connect(parent.captureImage)
        layout.addWidget(self.triggerOnce, 1, 0, 2, 1)

        self.trigger = QPushButton(f"{translation.STRINGS['picture_sequence'][language]}")
        self.trigger.setMinimumHeight(60)
        self.trigger.setMinimumWidth(150)
        self.trigger.clicked.connect(parent.captureSequence)
        layout.addWidget(self.trigger, 1, 1, 2, 1)

        self.progressBar = None

        self.pictureLabel = QLabel()
        bigFont = self.pictureLabel.font()
        bigFont.setPointSize(16)
        self.pictureLabel.setAlignment(Qt.AlignCenter)
        self.pictureLabel.setFont(bigFont)
        layout.addWidget(self.pictureLabel, 3, 0, 1, 2)

        self.setLayout(layout)
        self.layout = layout

        self.update(language, imgIndex, None)

    def update(self, language, imgIndex, progress):
        if progress is not None:
            if self.progressBar:
                if progress/hwUtils.FRAMES_PER_SEQUENCE == 1:
                    self.progressBar.setFormat(translation.STRINGS["saving_images"][language])
                else:
                    self.progressBar.setFormat(f"{translation.STRINGS['scanning'][language]}")
                self.progressBar.setValue(int(100*(progress/hwUtils.FRAMES_PER_SEQUENCE)))
            else:
                self.trigger.setEnabled(False)
                self.trigger.setVisible(False)
                self.triggerOnce.setEnabled(False)
                self.progressBar = QProgressBar()
                self.progressBar.setMinimumHeight(60)
                self.progressBar.setMinimumWidth(150)
                if progress/hwUtils.FRAMES_PER_SEQUENCE == 1:
                    self.progressBar.setFormat(translation.STRINGS["saving_images"][language])
                else:
                    self.progressBar.setFormat(f"{translation.STRINGS['scanning'][language]}")
                self.progressBar.setValue(int(100*(progress/hwUtils.FRAMES_PER_SEQUENCE)))
                self.layout.replaceWidget(self.trigger, self.progressBar)
        else:
            if self.progressBar:
                self.layout.replaceWidget(self.progressBar, self.trigger)
                self.progressBar.deleteLater()
                self.progressBar = None
                self.trigger.setEnabled(True)
                self.trigger.setVisible(True)
                self.triggerOnce.setEnabled(True)

        self.pictureLabel.setText(f"Pictures taken: {imgIndex}")
        if imgIndex == 0:
            self.pictureLabel.setStyleSheet("background-color : darkgreen")
        else:
            self.pictureLabel.setStyleSheet(None)

        self.triggerOnce.setText(translation.STRINGS["one_photo"][language])
        self.trigger.setText(translation.STRINGS["picture_sequence"][language])

class CamSettingsDialog(QDialog):
    def __init__(self, language, exposureTime, gain, camTemperature, parent=None):
        super().__init__(parent)

        self.setWindowTitle(translation.STRINGS["camera_settings"][language])

        dialogButtons = QDialogButtonBox.Ok | QDialogButtonBox.Cancel

        self.buttonBox = QDialogButtonBox(dialogButtons)
        self.buttonBox.button(QDialogButtonBox.Ok).setText(translation.STRINGS["ok"][language])
        self.buttonBox.button(QDialogButtonBox.Cancel).setText(translation.STRINGS["cancel"][language])
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        self.layout = QVBoxLayout()
        
        self.layout.addWidget(QLabel(translation.STRINGS["exposure_time"][language]))
        self.exposureEdit = QLineEdit(str(exposureTime))
        self.layout.addWidget(self.exposureEdit)

        self.layout.addWidget(QLabel(translation.STRINGS["gain"][language]))
        self.gainEdit = QLineEdit(str(gain))
        self.layout.addWidget(self.gainEdit)

        self.layout.addWidget(QLabel(f"{translation.STRINGS['temperature'][language]}: {camTemperature:.2f} °C"))

        self.layout.addWidget(self.buttonBox)
        self.setLayout(self.layout)

class MainWindow(QMainWindow):
    def __init__(self, machine_name):
        super().__init__()
        self.machine_name = machine_name
        self.imageAcquirer = None
        self.cam = None

    def start(self):
        self.language = translation.EN

        self.scanning = False
        self.capturedImages = []

        lastAnimal = server.lastAnimal()

        self.animalIndex = lastAnimal["id"]
        self.currentAnimal = lastAnimal
        self.progress = None

        outputDir = os.path.join(Path().absolute(), OUTPUT_DIR, str(self.animalIndex))
        self.imgIndex = 0
        if os.path.exists(outputDir):
            self.imgIndex = len([name for name in os.listdir(outputDir) if name.endswith(".png")])

        # setup camera
        self.h = Harvester()
        self.h.add_file(config['machine']['cti_path'])
        self.h.update()

        if len(self.h.device_info_list) > 0:
            for device in self.h.device_info_list:
                model = device.model.replace(" ", "_")

                cameraConfig = self.readCameraConfig(model)

                if cameraConfig is not None:
                    print(f"using camera {model}")
                    self.imageAcquirer = camUtils.initCam(self.h, device, cameraConfig, config["camera"]["preview_frame_rate"])

                    break

            if self.imageAcquirer is None:
                print(f"no configuration file for camera {model} found")
        else:
            print("no GenTL camera found")
        
        if self.imageAcquirer is None:
            print("using webcam (init may take some time)")
            self.cam = cv2.VideoCapture(0)
            
            if not self.cam.isOpened():
                print("no camera found")

        if config["machine"]["pi"]:
            self.externalTrigger = Button(hwUtils.PHYSICAL_TRIGGER_PIN, bounce_time=hwUtils.BOUNCE_TIME)
            self.externalTrigger.when_pressed = (
                lambda: QApplication.instance().postEvent(self, QEvent(QEvent.User))
            )

        self.setWindowTitle(f"BatScanner '{self.machine_name}'")

        menuBar = self.menuBar()
        self.settingsMenu = QMenu("&Settings", self)
        self.exportMenu = QMenu("&Export", self)
        languageMenu = QMenu("&Language/Idioma", self)
        menuBar.addMenu(self.settingsMenu)
        menuBar.addMenu(self.exportMenu)
        menuBar.addMenu(languageMenu)

        self.camSettingsAction = QAction(self)
        self.camSettingsAction.setText("Camera")
        self.camSettingsAction.triggered.connect(lambda: self.openCamSettings(self.imageAcquirer))

        if self.imageAcquirer is None:
            self.camSettingsAction.setEnabled(False)

        self.settingsMenu.addAction(self.camSettingsAction)

        for i, language in enumerate(translation.LANGUAGES):
            languageAction = QAction(self)
            languageAction.setText(language)
            languageAction.triggered.connect(partial(self.selectLanguage, translation.LANGUAGE_IDS[i]))
        
            languageMenu.addAction(languageAction)

        self.exportScansAction = QAction(self)
        self.exportScansAction.setText("Scans")
        self.exportScansAction.triggered.connect(lambda: self.exportScans(self.language))

        self.exportMenu.addAction(self.exportScansAction)

        scanLayout = QGridLayout()

        self.statusView = StatusGrid(self.language, self.currentAnimal, parent=self)
        scanLayout.addWidget(self.statusView, 0, 0)

        # Qt Cam
        self.feed = QLabel()
        self.feed.setMaximumHeight(utils.VIEWFINDER_HEIGHT)
        self.feed.setMaximumWidth(utils.VIEWFINDER_WIDTH)
        self.feed.setPixmap(QPixmap(os.path.join("graphics", "no_video_data.png")))
        scanLayout.addWidget(self.feed, 0, 1, 4, 1, alignment=Qt.AlignCenter)

        self.captureOverlay = QLabel()
        self.captureOverlay.setMaximumHeight(utils.VIEWFINDER_HEIGHT)
        self.captureOverlay.setMaximumWidth(utils.VIEWFINDER_WIDTH)
        scanLayout.addWidget(self.captureOverlay, 0, 1, 4, 1, alignment=Qt.AlignCenter)

        self.camControls = CamControlGrid(self.language, self.imgIndex, parent=self)
        scanLayout.addWidget(self.camControls, 1, 0)

        self.controls = ControlGrid(self.language, parent=self)
        scanLayout.addWidget(self.controls, 6, 0, 1, 2)

        scan = QWidget()
        scan.setLayout(scanLayout)
        self.layout = scanLayout

        self.setCentralWidget(scan)

        self.worker = CaptureWorker(self.imageAcquirer, self.cam)
        self.workerThread = QThread(self) 
        self.worker.moveToThread(self.workerThread)
        self.workerThread.start()

        self.worker.updateFeed.connect(self.updateFeed)
        self.worker.imageCaptured.connect(self.imageCaptured)

        QTimer.singleShot(0, self.worker.run)

        self.currentAnimal = lastAnimal
        self.controls.setAnimal(self.currentAnimal)

    def readCameraConfig(self, model):
        configPath = os.path.join('cameras', f'{model}.yaml')

        if os.path.isfile(configPath):
            with open(configPath) as f:
                config = yaml.safe_load(f)

            return config
        else:
            return None

    def event(self, event):
        if event.type() == QEvent.User:
            self.pedalPressed()
            return True

        return super().event(event)
    
    def updateFeed(self, image):
        self.feed.setPixmap(QPixmap.fromImage(image))

    def openCamSettings(self, imageAcquirer):
        exposureTime = camUtils.readExposureTime(imageAcquirer)
        gain = camUtils.readGain(imageAcquirer)
        camTemperature = camUtils.readTemperature(imageAcquirer)

        dlg = CamSettingsDialog(self.language, exposureTime, gain, camTemperature, self)
        dlg.accepted.connect(lambda: camUtils.setProperties(imageAcquirer, float(dlg.exposureEdit.text()), float(dlg.gainEdit.text())))
        dlg.exec()

    def exportScans(self, language):
        fileDlg = QFileDialog(self)

        fileDlg.setWindowTitle(translation.STRINGS["select_folder"][language])
        fileDlg.setFileMode(QFileDialog.FileMode.Directory)
        fileDlg.setViewMode(QFileDialog.ViewMode.List)

        if fileDlg.exec():
            targetDir = fileDlg.selectedFiles()[0]
            scanDir = os.path.join(Path().absolute(), OUTPUT_DIR)

            data = glob.glob(os.path.join(scanDir, '*'))
            numData = len(data)

            dlg = QProgressDialog(translation.STRINGS["copying_scans"][language].format(numData-1, targetDir), translation.STRINGS["cancel"][language], 0, numData, self)
            dlg.setWindowTitle(translation.STRINGS["export"][language])
            dlg.setWindowModality(Qt.WindowModal)

            for i, entry in enumerate(data):
                dlg.setValue(i)
                if dlg.wasCanceled():
                    return
                
                if os.path.isfile(entry):
                    shutil.copy(entry, os.path.join(targetDir, os.path.basename(entry)))
                else:
                    shutil.copytree(entry, os.path.join(targetDir, os.path.basename(entry)), dirs_exist_ok=True, ignore=shutil.ignore_patterns('small_*.jpg'))

            dlg.setValue(numData)

            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle(translation.STRINGS["information"][language])
            msg.setText(translation.STRINGS["copying_finished"][language].format(numData-1, targetDir))
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()

    def selectLanguage(self, languageId):
        self.language = languageId

        self.statusView.update(self.language, self.currentAnimal)
        self.camControls.update(self.language, self.imgIndex, self.progress)
        self.controls.translate(self.language)

        self.settingsMenu.setTitle(f"&{translation.STRINGS['settings'][self.language]}")
        self.exportMenu.setTitle(f"&{translation.STRINGS['export'][self.language]}")

        self.camSettingsAction.setText(translation.STRINGS['camera'][self.language])
        self.exportScansAction.setText(translation.STRINGS['scans'][self.language])

    def imageCaptured(self, image, previewImage):
        self.imgIndex += 1

        QTimer.singleShot(0, lambda: self.animate(previewImage))

        if self.scanning:
            outputDir = os.path.join(Path().absolute(), OUTPUT_DIR, str(self.animalIndex))
            imgPath = os.path.join(outputDir, f'{str(self.imgIndex).zfill(5)}.png')
            self.capturedImages.append((imgPath, image))
            self.progress += 1
            self.camControls.update(self.language, self.imgIndex, self.progress)

            if self.progress < hwUtils.FRAMES_PER_SEQUENCE/2:
                # forward
                self.stepperWorker = StepperWorker(0, hwUtils.ELEVATION_INTERVAL)
                self.th = QThread(self)
                self.stepperWorker.moveToThread(self.th)
                self.th.start()

                self.stepperWorker.finished.connect(self.stepperFinished)
                QTimer.singleShot(0, self.stepperWorker.run)
            elif self.progress < hwUtils.FRAMES_PER_SEQUENCE:
                # backward
                self.stepperWorker = StepperWorker(1, hwUtils.ELEVATION_INTERVAL)
                self.th = QThread(self)
                self.stepperWorker.moveToThread(self.th)
                self.th.start()

                self.stepperWorker.finished.connect(self.stepperFinished)
                QTimer.singleShot(0, self.stepperWorker.run)
            else:
                # finished
                hwUtils.reset()

                self.saveWorker = SaveWorker(self.capturedImages)
                self.saveThread = QThread(self)
                self.saveWorker.moveToThread(self.saveThread)
                self.saveThread.start()

                self.saveWorker.imagesSaved.connect(self.imagesSaved)
                QTimer.singleShot(0, self.saveWorker.run)
        else:
            # do not use a saveworker if it's just a single image
            outputDir = os.path.join(Path().absolute(), OUTPUT_DIR, str(self.animalIndex), "singleImages")
            imgPath = os.path.join(outputDir, f'{str(self.imgIndex).zfill(5)}.png')
            os.makedirs(outputDir, exist_ok=True)
            cv2.imwrite(imgPath, image)

            self.camControls.update(self.language, self.imgIndex, None)

    def imagesSaved(self, folderPath):
        self.saveThread.quit()
        del self.saveWorker
        self.saveWorker = None

        self.scanning = False
        self.capturedImages.clear()
        self.controls.next.setEnabled(True)
        self.progress = None
        self.camControls.update(self.language, self.imgIndex, None)
    
    def __del__(self):
        if self.worker is not None:
            QTimer.singleShot(0, lambda: self.worker.stopCapturing())

            self.workerThread.quit()
            self.workerThread.wait()

        if self.imageAcquirer is not None:
            self.imageAcquirer.destroy()

        if self.h is not None:
            self.h.reset()

        if self.cam is not None:
            self.cam.release()

    def pedalPressed(self):
        # do not start scanning if there is already a scan in progress
        if not self.scanning:
            self.captureSequence()

    def captureSequence(self):
        self.scanning = True
        self.progress = 0

        self.controls.next.setEnabled(False)
        self.setFocus() # this prevents the text fields from being focused when the buttons are disabled

        self.camControls.update(self.language, self.imgIndex, self.progress)

        self.stepperWorker = StepperWorker(0, hwUtils.ELEVATION_INTERVAL)
        self.th = QThread(self)
        self.stepperWorker.moveToThread(self.th)
        self.th.start()

        self.stepperWorker.finished.connect(self.stepperFinished)
        QTimer.singleShot(0, self.stepperWorker.run)

    @pyqtSlot()
    def stepperFinished(self):
        self.th.quit()
        del self.stepperWorker
        self.stepperWorker = None

        self.captureImage()

    def captureImage(self):
        QTimer.singleShot(0, lambda: self.worker.captureImage())

    def animate(self, img):
        self.captureOverlay.setStyleSheet("border: 3px solid red;")
        self.captureOverlay.setPixmap(QPixmap.fromImage(img).scaledToHeight(utils.VIEWFINDER_HEIGHT - 6))

        QTimer.singleShot(utils.FREEZE_TIME, self.resetAnimation)

    def resetAnimation(self):
        self.captureOverlay.setStyleSheet("border: none;")
        self.captureOverlay.clear()

    # ---------------------------------------------------------------------------
    # Server connection
    # Send the info to the Server
    def save(self, remarks, externalIndex, bag, species, recapture, maintenanceData):
        if len(externalIndex) > 0:
            animals = server.getAnimals()

            duplicates = False

            for animal in animals:
                if animal["foreign_id"] == externalIndex:
                    duplicates = True
                    break

            if duplicates:
                self.controls.externalIndexEdit.setStyleSheet("background-color : darkred")
                self.statusView.statusLabel.setStyleSheet("background-color : darkred")
                self.statusView.statusLabel.setText(translation.STRINGS["duplicate_ext_id"][self.language])
            else:
                self.controls.externalIndexEdit.setStyleSheet(None)
                self.statusView.statusLabel.setStyleSheet(None)
                self.statusView.statusLabel.clear()
        else:
            self.controls.externalIndexEdit.setStyleSheet(None)
            self.statusView.statusLabel.setStyleSheet(None)
            self.statusView.statusLabel.clear()

        self.currentAnimal["foreign_id"] = externalIndex
        self.currentAnimal["name"] = bag
        self.currentAnimal["description"] = remarks
        self.currentAnimal["species_id"] = species_dict[species]
        self.currentAnimal["recapture"] = recapture
        self.currentAnimal["maintenance_data"] = maintenanceData

        server.changeAnimal(
            self.currentAnimal["id"],
            self.currentAnimal["foreign_id"],
            self.currentAnimal["name"],
            self.currentAnimal["description"],
            self.currentAnimal["species_id"],
            self.currentAnimal["recapture"],
            self.currentAnimal["maintenance_data"]
        )

        self.controls.saveButton.setEnabled(False)

    # Adds a new animal at the server with empty data.
    def nextAnimal(self):
        new_animal = server.newAnimal()

        self.animalIndex = new_animal["id"]
        self.currentAnimal = new_animal

        self.imgIndex = 0
        self.camControls.update(self.language, self.imgIndex, self.progress)
        self.controls.clear()

        self.statusView.statusLabel.setStyleSheet(None)
        self.statusView.statusLabel.clear()
        self.statusView.update(self.language, new_animal)

    # Animal edited at the server, set the texts and in QT
    def updateAnimal(self, animal, species_name):
        self.currentAnimal = animal

        self.statusView.update(self.language, self.currentAnimal)
        self.controls.externalIndexEdit.setText(animal["foreign_id"])
        self.controls.remarks.setText(animal["description"])
        self.controls.bag.setText(animal["name"])
        # Index in the dropdown is not the ID as in species_dict!
        self.controls.species.setCurrentIndex(species_list.index(species_name))
        self.controls.maintenanceBox.setChecked(animal["maintenance_data"])
        self.controls.recaptureBox.setChecked(animal["recapture"])

        self.controls.saveButton.setEnabled(False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()

    print("Machine:", config['machine']['name'])

    production_mode = args.production
    OUTPUT_DIR = os.path.join("static", "capture", config["machine"]["name"])

    species_dict = {}
    species_list = []
    
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()

    window = MainWindow(config["machine"]["name"])

    # ===================================================================
    # Server
    # ===================================================================

    # Start the server in a new thread
    batServer = ServerThread(config["machine"]["name"], config["machine"]["port"])
    batServer.animalChanged.connect(window.updateAnimal)

    batServer.start()

    # Wait for the server to start....
    time.sleep(3)

    # get the Species
    server = BatServerConnector(config["machine"]["port"])
    species_db = server.getSpecies()
    for s in species_db:
        species_list.append(s["name"])
        species_dict[s["name"]] = s["id"]

    # ===================================================================

    window.start()

    if production_mode:
        window.showMaximized()
    else:
        window.setFixedSize(1024, 600)
        window.show()
    
    sys.exit(app.exec())
