#!/usr/bin/env python3
import RPi.GPIO as GPIO
from datetime import datetime, date, timedelta
import spidev
import board
import neopixel
import BlynkLib
import time
import threading
from threading import Thread
from googleapiclient.discovery import build  
from httplib2 import Http  
from oauth2client import file, client, tools  
from oauth2client.service_account import ServiceAccountCredentials
import os
import smtplib
import gc
import psutil

#Email credentials
Email_user = ''                                                     #!insert email adress
Email_pw = ''                                                       #!insert password for log in to the email account

#general variables
mosfet_gpio = 12                                                    #defines the GPIO pin for the MOSFET
level_sensor_pin = 17                                               #defines the GPIO pin for the water level sensor
led_gpio = board.D18                                                #defines the GPIO pin for the LED
led_num = 25                                                        #defines the amount of LED pixels
led_order = neopixel.RGB                                            #defines the color code in which control the LEDs
led_brightness = 255                                                #defines the brightness of the LED's (0-255)
pump_pwm = 0                                                        #is the value which controls the pump (gets changed during the script)
blynk_manual_watering = 0                                           #is the button value coming from the BLYNK app (gets changed during the script)
blynk_pump_block = 0
light_value = 1023                                                  #is either 0 or 1 depending if the LED's are on or off
moisture_value = 1023                                               #sensor data (gets changed during the script)
if_watering = False                                                 #boolean variable (gets changed during the script)
led = 0                                                             #set to zero at the beginning 
moisture_value_percent = 0.00                                       #in percent
waterlevel = False                                                  #variables that defines if there is still water in the tank or  not
moisture_value_list = []                                            #in this list the latest 30 values of the moisture sensor will be stored --> reference for average
light_value_list = []                                               #in this list the latest 30 values of the moisture sensor will be stored --> reference for average
adressee = 'testlogin.felix@gmail.com'                              #this adresse will recieve an email when the waterlevel in the tank is low
led_toggle = 0
online_since = datetime.now()


#rest time
light_on_time = 0 #initially zero
ligh_off_time = 0 #initially zero

#time variables
blynk_start_time = datetime.now()                                   #reference time stamp
current_time = datetime.now()                                       #reference time stamp    
time_delta = 10                                                     #reference time stamp
min_watering_difference = timedelta(minutes=30)                     #minimal time difference between 2 watering processes
watering_start_time = datetime.now()                                #reference time stamp
last_time_watered = datetime.now() - min_watering_difference + timedelta(seconds=30)              #reference time stamp
timestamp_empty_tank = datetime.now() - timedelta(days=1)           #reference time stamp

#blocker
pump_block = int(0)                                                 #This variable ensures that there can only run one watering process at one time

#define LED
pixels = neopixel.NeoPixel(led_gpio, led_num, brightness=led_brightness, auto_write=False,
                           pixel_order=led_order)                   #defines the WS2813 LED strip

# Initialize Blynk
blynk = BlynkLib.Blynk('SDqOH9cUOAYnwuqnCISQIko32dUkOlZ-') #use you own token (according to email)

#register virtual Blynk pins
@blynk.VIRTUAL_WRITE(2)                                             #Reads the button value from the BlynkApp, setting in BlynkApp --> "Output" V2
def my_write_handler(value):
    global blynk_manual_watering
    blynk_manual_watering = int(value[0])
    if blynk_manual_watering == 1:
        watering()


@blynk.VIRTUAL_WRITE(1)                                             #Reads the button value from the BlynkApp, setting in BlynkApp --> "Output" V1
def my_write_handler(value):
    global pump_block
    blynk_pump_block = int(value[0])
    if blynk_pump_block == 1:
        pumpstop()
    
    
@blynk.VIRTUAL_WRITE(5)                                             #Reads the button value from the BlynkApp, setting in BlynkApp --> "Output" V5
def my_write_handler(value):
    global led_toggle
    led_toggle_button = int(value[0])
    if led_toggle_button == 1:
        if led_toggle == 0:
            led_toggle = 1
        elif led_toggle == 1:
            led_toggle = 0

@blynk.VIRTUAL_WRITE(6)                                             #Reads the button value from the BlynkApp, setting in BlynkApp --> "Output" V6
def my_write_handler(value):
    global last_time_watered
    global min_watering_difference
    #cooldown reset button
    cooldown_button = int(value[0])
    if cooldown_button == 1:
        last_time_watered = last_time_watered - min_watering_difference



#port setup
GPIO.setmode(GPIO.BCM)                                              #Setmode is either BOARD or BCM, depening on with which alignment you would like to work

GPIO.setup(mosfet_gpio,GPIO.OUT)                                    #Defines that the Pin "mosfet_gpio" is an output
mosfet_output = GPIO.PWM(mosfet_gpio,100)                           #Defines on which frequency the PWM signal is working --> 100 == fequency 100Hz -> recommended value 100
GPIO.setup(level_sensor_pin,GPIO.IN,pull_up_down=GPIO.PUD_UP)       #Defines that the level sensor is an input, pull_up_down defines, that of there is voltage the signal = ON

# initialization of the analog pins
A0 = 0                                                              #defines the input pin of the Moisture sensor
A2 = 2                                                              #defines the input pin of the Light sensor 

# SPI-settings
spi = spidev.SpiDev()                                               #settings for the SPI protocol
spi.open(0,0)                                                       #settings for the SPI protocol
spi.max_speed_hz = 2000000                                          #settings for the SPI protocol

def readadc(adcnum):                                                #function that reads an analog signal (argument "adcnum" will later be the pin you used)
# read SPI-values
 r = spi.xfer2([1,8+adcnum <<4,0])
 adcout = ((r[1] &3) <<8)+r[2]
 return adcout

#google logging
MY_SPREADSHEET_ID = ''                                              #!spreadsheet ID of the google sheet

def get_average(lst):                                               #function that returns the average value of a list
    if len(lst) != 0:
        return sum(lst) / len(lst) 
    else:
        time.sleep(3)

# Initialize Google Spread Sheet API                                #credentials for the google sheet API 
SCOPES = 'https://www.googleapis.com/auth/spreadsheets'
creds = ServiceAccountCredentials.from_json_keyfile_name( 
		'credentials.json', SCOPES)                                 #credentials.json file must be downloaded from the google api console and saved in the same project folder on the raspberry
service = build('sheets', 'v4', http=creds.authorize(Http()))
		
def update_sheet(sheetname, moisture, speed, ledlight, daylight):  #function that sends values to the google sheet
    
    global led
    global moisture_value
    global pump_pwm
    global light_value
    global service

    # Call the Sheets API, append the next row of sensor data
    # values is the array of rows we are updating, its a single row
    log_time = datetime.now()
    log_time_hours = log_time.hour
    log_time_minutes = log_time.minute
    log_time_seconds = log_time.second
    log_time_format = str(str(log_time_hours)+ ":" + str(log_time_minutes) + ":" + str(log_time_seconds))

    values = [ [ str(log_time.date()), str(log_time), log_time_format,
        moisture_value, pump_pwm, led, light_value]]
    cpuload1 = psutil.cpu_percent()
    body = { 'values': values }
    # call the append API to perform the operation
    result = service.spreadsheets().values().append(
                spreadsheetId=MY_SPREADSHEET_ID, 
                range=sheetname + '!A1:G1',
                valueInputOption='USER_ENTERED', 
                insertDataOption='INSERT_ROWS',
                body=body).execute()
    #print("send request completed")
    
	# Cleanup resources
    log_time_format = None
    values = None
    result = None

    time.sleep(20)
	
    exit()
    
def sheet_updater():                                                #function creates a new thread for the update__sheet() function
    sheet_update_thread = Thread(target = update_sheet, args=("Vertical_Farm", moisture_value, pump_pwm, led, light_value))
    sheet_update_thread.start()
    #print("request pending")

def send_data_to_sheet():
    while True:
        sheet_updater()
        time.sleep(15)

def watering():                                                     #waters the plant for 10seconds, checks for the last time of watering to prevent overflowing
    #print("try to water")
    global if_watering
    global last_time_watered
    if if_watering == False:
        #print("watering false")
        if last_time_watered + min_watering_difference < datetime.now():
            #print("passed last time watered check")
            if pump_block == 0:
                thread = Thread(target = autostop)
                thread.start() 
                global pump_pwm
                pump_pwm = 50
                sheet_updater()
                last_time_watered = datetime.now()
                if_watering = True
                    
            elif pump_block == 1:
                pumpstop()
        #else:
            #print("Wait for the next watering")

def autostop():                                                     #stops the pump after 10 seconds
    timestamp = datetime.now()
    while True:
        if pump_block == 1:
            pumpstop()
            break 
        
        if timedelta(seconds=10) < datetime.now() - timestamp:
            pumpstop()
            break

             

def pumpstop():                                                     #turns off the pump
    global pump_pwm
    pump_pwm = 0
    global if_watering
    if_watering = False 

def lighting(x):                                                    #toggles the LED's
    global led
    if x :
        pixels.fill((80, 70, 255))
        pixels.show()
        led = 1
    else:
        pixels.fill((0, 0, 0))
        pixels.show()
        led = 0

def automatic_lighting():                                           #turns the LED's on at a certain enviroment light level
    if light_value < 400:
        lighting(True)
    elif led_toggle == 1:
        lighting(True)
    else:
        lighting(False)

def automatic_watering():                                           #waters the plant at a certain moisture value
        if moisture_value < 250:
            watering()
        
#def check_for_blynk_manual_watering():                              #checks the BlynkApp button values
#        if int(blynk_manual_watering) == 1:
#            watering()

#def check_for_blynk_pumpstop():                                     #checks the BlynkApp button values
#        if int(blynk_pump_block) == 1:
#            pumpstop()
#            print("stop " + blynk_pump_block)

def send_values_to_blynk():                                         #sends moisture value in percent to the BlynkApp
    blynk.virtual_write(3, str(moisture_value_percent)) 
    blynk.virtual_write(7, str(last_time_watered))
    blynk.virtual_write(8, str(online_since))

def read_moisture_sensor():                                         #reads the moisture value, adding the value to a list and takes average of that list
    #print("read moisture")
    global moisture_value_list
    global moisture_value
    if len(moisture_value_list) > 60:
        moisture_value_list.pop(0)
    moisture_value_list.append(round(readadc(A0), 2))
    moisture_value = round(get_average(moisture_value_list),0)
    
def read_light_sensor():                                            #reads the light value, adding the value to a list and takes average of that list
    global light_value_list
    global light_value
    if len(light_value_list) > 60:
        light_value_list.pop(0)
    light_value_list.append(round(readadc(A2),2))
    light_value = round(get_average(light_value_list),0) 
    
def waterlevel_toggle():                                            #detects if the waterlevel in the tank is low, sends an alert email
    global waterlevel
    timedelta_mail_reminder = timedelta(minutes=50)
    global timestamp_empty_tank
    if (GPIO.input(level_sensor_pin)) == 1:
        if waterlevel == False: #no more water in storage
            timestamp_empty_tank = datetime.now()
            waterlevel = True
            send_email_notification()
        if timestamp_empty_tank + timedelta_mail_reminder < datetime.now():     #resends the alert after a certain time
            send_email_notification()
            timestamp_empty_tank = datetime.now()

    elif (GPIO.input(level_sensor_pin)) == 0:
        waterlevel = False
                
def send_email_notification():                                      #sends an email to a certain adres
    with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()

        smtp.login(Email_user, Email_pw)

        subject = "SmartPlant Alert - Water tank is on low level"
        body = "Your water tank runs out of water. Refill soon!"

        msg = f'Subject: {subject}\n\n{body}'

        smtp.sendmail(adressee, Email_user, msg)

def thread_functions():
    while True:
        waterlevel_toggle()
        read_moisture_sensor()
        read_light_sensor()
        send_values_to_blynk()
        time.sleep(.5)

time.sleep(5)

print("system running")

mosfet_output.start(0) 

second_thread = Thread(target= thread_functions)
second_thread.start()

update_sheet_thread = Thread(target = send_data_to_sheet)
update_sheet_thread.start()

gc.enable()
sheet_updater()

while True:
    try:
        blynk.run()
        automatic_watering()
        automatic_lighting()
        #check_for_blynk_manual_watering()
        #heck_for_blynk_pumpstop()
        mosfet_output.ChangeDutyCycle(pump_pwm)
        moisture_value_percent = round(100 / 1023 * moisture_value, 2)
        time.sleep(.1)
                    
    except KeyboardInterrupt:
        print("System shutdown")
        pumpstop()
        pump_pwm = 0
        lighting(False)
        #print_values.join()
        print("Successful shutdown")
        exit()