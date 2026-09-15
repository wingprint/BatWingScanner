import time
import yaml

############################ physical trigger (pedal)

PHYSICAL_TRIGGER_PIN = 14
BOUNCE_TIME = 0.1

############################ stepper motor controls

step_index = 0

# stepper motor constants
DELAY = 0.005
STEPS_IN_ROTATION = 2048 # 2048 Full Steps or 4096 Half Steps
DRIVE_RATIO = 1/3 # Our drive has a 1:3 reduction
MAX_ELEVATION = 30
FRAMES_PER_SEQUENCE = 18
ELEVATION_INTERVAL = int(MAX_ELEVATION/(FRAMES_PER_SEQUENCE/2))

# same order as on the ULN2003 driver board (IN1-IN4)
COIL_A_PIN = 8
COIL_B_PIN = 9
COIL_C_PIN = 11
COIL_D_PIN = 25

STEP_SEQUENCE = [
    [0, 0, 1, 1],
    [0, 1, 1, 0],
    [1, 1, 0, 0],
    [1, 0, 0, 1],
]

with open('config.yaml') as f:
    config = yaml.safe_load(f)

if config["machine"]["pi"]:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(COIL_A_PIN, GPIO.OUT)
    GPIO.setup(COIL_B_PIN, GPIO.OUT)
    GPIO.setup(COIL_C_PIN, GPIO.OUT)
    GPIO.setup(COIL_D_PIN, GPIO.OUT)

def setStep(a, b, c, d):
    if config["machine"]["pi"]:
        GPIO.output(COIL_A_PIN, a)
        GPIO.output(COIL_B_PIN, b)
        GPIO.output(COIL_C_PIN, c)
        GPIO.output(COIL_D_PIN, d)
    else:
        return

def forward(angle):
    global step_index

    steps = int(angle/(360.0/STEPS_IN_ROTATION)/DRIVE_RATIO)
    
    for _ in range(steps):
        step_index = (step_index+1) % len(STEP_SEQUENCE)

        setStep(*STEP_SEQUENCE[step_index])
        time.sleep(DELAY)
 
def backwards(angle):
    global step_index

    steps = int(angle/(360.0/STEPS_IN_ROTATION)/DRIVE_RATIO)
    
    for _ in range(steps):
        step_index = (step_index-1) % len(STEP_SEQUENCE)

        setStep(*STEP_SEQUENCE[step_index])
        time.sleep(DELAY)

def reset():
    setStep(0, 0, 0, 0)

