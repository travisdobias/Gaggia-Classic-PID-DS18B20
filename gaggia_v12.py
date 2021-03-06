#!/usr/bin/env python3

"""

Gaggia_v12.py: This is a simple python script to run a PID temp controller on a
Gaggia Classic coffee machine using a raspberry pi and a DS18B20 temp sensor.

__author__      = "Travis Dobias"
__copyright__   = "Copyright 2018"
__email__       = "travis@dobias.be"

For temperature monitoring, this uses a DS18B20 style probe attached to the
outside of the boiler.  NB this doesn't support the steam feature yet.

For boiler control, this uses a FOSTEK style SSR rated 25A
For Pump control, this uses a FOSTEK style SSR rated 25A

Please make sure you are familiar with household electricity!  Disconnect
the coffee machine from the main current before working inside the machine.

# GPIO PINs
# PIN 1  - 3V3 for DS18b20
# PIN 6  - ground for DS18b20
# PIN 8  - GPIO14 signal to DS18B20 sensor
# PIN 14 - ground for Fostek boiler
# PIN 16 - GPIO23 output to control boiler SSR
# PIN 18 - GPIO24 output to control pump SSR - yellow wire
# PIN 20 - ground - orange wire for SSR
# PIN 17 - 3.3v for brew switch - brown wire
# PIN 13 - GPIO 27 for brew switch - green wire

you can run the program from a ssh session, but if the session hangs, the
program will hang, and the boiler should cut off.

Another way of running it is with the no-hang up feature:
sudo nohup python3 gaggia_v12.py &

or modify the program to add a UI, LCD or other features!

"""

import os
import glob
import time
import RPi.GPIO as GPIO
import signal
import sys
from datetime import datetime

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(23, GPIO.OUT)
GPIO.setup(24, GPIO.OUT)
GPIO.setup(27, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

os.system('modprobe w1-gpio')
os.system('modprobe w1-therm')

base_dir = '/sys/bus/w1/devices/'
device_folder = glob.glob(base_dir + '28*')[0]
device_file = device_folder + '/w1_slave'

# below is a  piece of code that allows you trap an exit and turn off the
# boiler.  Without it, there is a risk that the GPIO pins will be left
# in the on state and the boiler will overheat since the stock thermostat
# is disconnected.
# https://stackoverflow.com/questions/18499497/how-to-process-sigterm-signal-gracefully

class GracefulKiller:
  kill_now = False
  def __init__(self):
    signal.signal(signal.SIGINT, self.exit_gracefully)
    signal.signal(signal.SIGTERM, self.exit_gracefully)

  def exit_gracefully(self,signum, frame):
    self.kill_now = True

# below is the PID controller, could be written as a function, but I created
# it as a class because why not.

class PIDController:
    def __init__(self, setpoint, antiwindup, Kp, Kd, Ki):
        self.setpoint = setpoint
        self.previous_delta = 0
        self.delta = 0
        self.antiwindup = antiwindup
        self.integral = 0
        self.derivative = 0
        self.Kp = Kp
        self.Kd = Kd
        self.Ki = Ki
        self.dt = 1
        self.output = 0
        self.boiler = 0
        self.sensor_reading = 0

    def calc(self, x):

        # Basic PID forumla with an antiwindup feature
        self.sensor_reading = x
        self.previous_delta = self.delta
        self.delta = self.setpoint - self.sensor_reading
        if self.sensor_reading > self.setpoint-self.antiwindup:
            if self.sensor_reading < self.setpoint+self.antiwindup:
                self.integral = self.integral + (self.delta * self.dt)
        self.derivative = (self.delta - self.previous_delta) / self.dt
        self.output = int((self.delta * self.Kp) + (self.Kd * self.derivative) + (self.integral * self.Ki))

        # heat the boiler elements if the PID output is positive
        if self.output>0:
            if self.sensor_reading>0: #safety catch incase PID is in error state
                if self.output>100:
                    self.output=100
                boilerPWM.start(self.output)
                self.boiler=self.output
            else:
                boilerPWM.stop()
        else:
            boilerPWM.stop()
            self.boiler=0

# this is the temp reading functions for the DS18B20 borrowed from circuitbasics
# http://www.circuitbasics.com/raspberry-pi-ds18b20-temperature-sensor-tutorial/

def read_temp_raw():
    f = open(device_file, 'r')
    lines = f.readlines()
    f.close()
    return lines

def read_temp():
    lines = read_temp_raw()
    while lines[0].strip()[-3:] != 'YES':
        time.sleep(0.2)
        lines = read_temp_raw()
    equals_pos = lines[1].find('t=')
    if equals_pos != -1:
        temp_string = lines[1][equals_pos+2:]
        temp_c = float(temp_string) / 1000.0

        return temp_c

# this function is triggered when the brewbutton is switched on
def BrewButton():

    # start pump (gaggia pump switch is rewired to a FOSTEK SSR)
    GPIO.output(24,GPIO.HIGH)

    # this runs the boiler while the pump is brewing to maintain the water
    # temp as the boiler pulls in cold water from the tank
    boost=30
    while GPIO.input(27):
        boost=boost+5
        sensor_reading=read_temp()
        if boost>85:
            boost=85
        if sensor_reading < setpoint:
            boilerPWM.start(boost) # heat boiler while pump is running to maintain temp
        else:
            boilerPWM.stop()

        if verbose:
            print(time.strftime("%H:%M:%S"), "{0:8.3f}".format(sensor_reading), "{0:8.0f}".format(boost), "{0:8.1f}".format(0), "{0:8.1f}".format(0), "{0:8.1f}".format(0), "{0:8.1f}".format(0),"{0:>8s}".format('Brew'))
    GPIO.output(24,GPIO.LOW) #  turn off pump
    boilerPWM.stop() #boiler off

#main
killer = GracefulKiller()

# the boiler control uses artifical pulse-width modulation of the Pi
# PIN 23 is assigned to the Fostek SSR in this example
# 50 HZ is the switch rate which should allow the SSR to switch on/off
# in line with the AC phases of the 220v mains current
boilerPWM=GPIO.PWM(23, 50)

# environment variables; could be put in an include file
logfilename = datetime.now().strftime('PIDlogfile_%Y-%m-%d_%H-%M-%S') +".csv"
integral = 0
delta = 0
derivative = 0
setpoint = 96.5
dt = 1
Kp = 6
Ki = 0.05 #0.03
Kd = 48  #.030 is 1.1 degree
antiwindup = 2
logging = True
verbose = True

# instantiate PID object
PID=PIDController(setpoint, antiwindup, Kp, Kd, Ki)

if logging:
    logfile = open(logfilename, "w+")
    logfile.write("Gaggia PID Started: "+ str(os.path.basename(__file__))+"\r\n")
    logfile.write("Press control-c or kill " +str(os.getpid())+ " to end.")
    logfile.write("Setpoint:,"+str(setpoint)+"\r\ndt:,"+str(dt)+"\r\nKp:,"+str(Kp)+"\r\nKi:,"+str(Ki)+"\r\nKd:,"+str(Kd)+"\r\n")
    logfile.write("Time,temp,boiler,Out,P,I,D,state\r\n")

print("Gaggia PID Started:", os.path.basename(__file__))
if verbose:
    print("Press control-c or kill " +str(os.getpid())+ " to end.")
    print("setpoint:",setpoint,"\r\ndt:",dt,"\r\nKp:",Kp,"\r\nKi:",Ki,"\r\nKd:",Kd)
    print("Time","{0:>12s}".format("Temp"),"{0:>8s}".format("boiler"),"{0:>8s}".format("Out"),"{0:>8s}".format("P"),"{0:>8s}".format("I"),"{0:>8s}".format("D"),"{0:>8s}".format("action"))
else:
    print("terminal output suppressed.  Press control-c or kill " +str(os.getpid())+ " to end.")

#MAIN LOOP
while True:

    # the brew switch of the gaggia is rewired to the GPIO to run the
    # pump and boiler at the same time in the BrewButton function
    if GPIO.input(27):
        BrewButton()

    # get the temperature reading from the sensor
    sensor_reading = read_temp()

    # update the PID
    PID.calc(sensor_reading)

    # logging features
    if verbose:
        print(time.strftime("%H:%M:%S"), "{0:8.3f}".format(PID.sensor_reading), "{0:8.0f}".format(PID.boiler), "{0:8.1f}".format(PID.output),"{0:8.1f}".format(PID.delta * PID.Kp),"{0:8.1f}".format(PID.integral * PID.Ki),"{0:8.1f}".format(PID.Kd * PID.derivative),"{0:>8s}".format('PID'))

    if logging:
        logfile.write(time.strftime("%H:%M:%S")+","+str(PID.sensor_reading)+","+str(PID.boiler)+","+str(PID.output)+","+str((PID.delta * PID.Kp))+","+str((PID.integral * PID.Ki))+","+str((PID.Kd * PID.derivative))+",PID\r\n")

    # this traps errors including keyboard interrupt to switch the boiler
    # off safely in case of exit or kill of the process

    if killer.kill_now:
        if logging:
            logfile.close()
        boilerPWM.stop()
        GPIO.cleanup() # this ensures a clean exit
        print("---GPIOs released--")
        print("---Program end--")
        break
