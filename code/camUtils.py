def initCam(harvester, deviceInfo, cameraConfig, previewFrameRate):
    imageAcquirer = harvester.create(deviceInfo)

    imageAcquirer.remote_device.node_map.PixelFormat.value = cameraConfig['cam']['image_format']
    imageAcquirer.remote_device.node_map.AcquisitionFrameRate.value = previewFrameRate
    imageAcquirer.remote_device.node_map.Width.value = int(cameraConfig['cam']['width'])
    imageAcquirer.remote_device.node_map.Height.value = int(cameraConfig['cam']['height'])
    imageAcquirer.remote_device.node_map.ExposureAuto.value = "Off"
    imageAcquirer.remote_device.node_map.ExposureTime.value = float(cameraConfig['cam']['exposure_time'])*1000000
    imageAcquirer.remote_device.node_map.GainAuto.value = "Off"
    imageAcquirer.remote_device.node_map.Gain.value = float(cameraConfig['cam']['gain'])
    imageAcquirer.remote_device.node_map.ReverseY.value = True
    imageAcquirer.remote_device.node_map.BalanceWhiteAuto.value = "Once"

    imageAcquirer.start()

    return imageAcquirer

def readTemperature(imageAcquirer):
    return imageAcquirer.remote_device.node_map.DeviceTemperature.value

def readGain(imageAcquirer):
    return imageAcquirer.remote_device.node_map.Gain.value

def readExposureTime(imageAcquirer):
    return imageAcquirer.remote_device.node_map.ExposureTime.value/1000000

def setExposureTime(imageAcquirer, exposureTime):
    imageAcquirer.remote_device.node_map.ExposureTime.value = exposureTime*1000000

def setGain(imageAcquirer, gain):
    imageAcquirer.remote_device.node_map.Gain.value = gain

def setProperties(imageAcquirer, exposureTime, gain):
    setExposureTime(imageAcquirer, exposureTime)
    setGain(imageAcquirer, gain)