#!/usr/bin/env python3
import RPi.GPIO as GPIO
from datetime import datetime, date, timedelta
import board
import neopixel
import Adafruit_ADS1x15
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
adressee = ''                                                       #!this adresse will recieve an email when the waterlevel_cm in the tank is low

#general variables
mosfet_gpio = 12                                                    #defines the GPIO pin for the MOSFET
level_sensor_pin = 17                                               #defines the GPIO pin for the water level sensor
led_gpio = board.D18                                                #defines the GPIO pin for the LED #Board18 = BCM 24
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
moisture_value_list = []                                            #in this list the latest 30 values of the moisture sensor will be stored --> reference for average
light_value_list = []                                               #in this list the latest 30 values of the moisture sensor will be stored --> reference for average
led_toggle = 0
online_since = datetime.now()
watertank_height = 40.0                                             #!adjust this value to the height of your watertank (unit cm!)
refill_reminder_height = 10.00                                      #!adjust this value to the waterheight, when you'd like to get the notification to refill the tank (unti cm!)
waterlevel_cm = watertank_height                                    #variables that defines if there is still water in the tank or  not


#rest time
light_on_time = 0 #initially zero
ligh_off_time = 0 #initially zero

#time variables
blynk_start_time = datetime.now()                                   #reference time stamp
min_watering_difference = timedelta(minutes=30)                     #minimal time difference between 2 watering processes
watering_start_time = datetime.now()                                #reference time stamp
last_time_watered = datetime.now() - min_watering_difference + timedelta(seconds=30)              #reference time stamp
timestamp_empty_tank = datetime.now() - timedelta(days=1)           #reference time stamp
LastMailReminder = datetime.now() - timedelta(days=1)

#blocker
pump_block = int(0)                                                 #This variable ensures that there can only run one watering process at one time

#define LED
pixels = neopixel.NeoPixel(led_gpio, led_num, brightness=led_brightness, auto_write=False,
                           pixel_order=led_order)                   #defines the WS2813 LED strip

# Initialize Blynk
blynk = BlynkLib.Blynk('')                                          #!use you own token (according to email)

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
    
    
@blynk.VIRTUAL_WRITE(4)                                             #Reads the button value from the BlynkApp, setting in BlynkApp --> "Output" V5
def my_write_handler(value):
    global led_toggle
    led_toggle_button = int(value[0])
    if led_toggle_button == 1:
        if led_toggle == 0:
            led_toggle = 1
        elif led_toggle == 1:
            led_toggle = 0

@blynk.VIRTUAL_WRITE(5)                                             #Reads the button value from the BlynkApp, setting in BlynkApp --> "Output" V6
def my_write_handler(value):
    global last_time_watered
    global min_watering_difference
    #cooldown reset button
    cooldown_button = int(value[0])
    if cooldown_button == 1:
        last_time_watered = datetime.now() - min_watering_difference



#port setup
GPIO.setmode(GPIO.BCM)                                              #Setmode is either BOARD or BCM, depening on with which alignment you would like to work

GPIO.setup(mosfet_gpio,GPIO.OUT)                                    #Defines that the Pin "mosfet_gpio" is an output
mosfet_output = GPIO.PWM(mosfet_gpio,100)                           #Defines on which frequency the PWM signal is working --> 100 == fequency 100Hz -> recommended value 100
GPIO.setup(level_sensor_pin,GPIO.IN,pull_up_down=GPIO.PUD_UP)       #Defines that the level sensor is an input, pull_up_down defines, that of there is voltage the signal = ON

# initialization of the analog pins
A0 = 0                                                              #defines the input pin of the Moisture sensor
A1 = 1                                                              #defines the input pin of the Light sensor 

adc = Adafruit_ADS1x15.ADS1015()



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
        
def update_sheet(sheetname, moisture, speed, illumination, lightsensor, waterheight):  #function that sends values to the google sheet
    
    global led
    global moisture_value
    global pump_pwm
    global light_value
    global service
    global waterlevel_cm

    # Call the Sheets API, append the next row of sensor data
    # values is the array of rows we are updating, its a single row
    log_time = datetime.now()
    log_time_hours = log_time.hour
    log_time_minutes = log_time.minute
    log_time_seconds = log_time.second
    log_time_format = str(str(log_time_hours)+ ":" + str(log_time_minutes) + ":" + str(log_time_seconds))

    values = [ [ str(log_time.date()), str(log_time), log_time_format,
        moisture_value, pump_pwm, led, light_value, waterheight]]
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
    sheet_update_thread = Thread(target = update_sheet, args=("SmartPlant", moisture_value, pump_pwm, led, light_value, waterlevel_cm)) #"SmartPlant" is the name of the register in your Google sheet
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
    if light_value > 400:
        lighting(True)
    elif led_toggle == 1:
        lighting(True)
    else:
        lighting(False)

def automatic_watering():                                           #waters the plant at a certain moisture value
        if moisture_value < 250:
            watering()
        
def send_values_to_blynk():                                         #sends moisture value in percent to the BlynkApp
    blynk.virtual_write(3, str(moisture_value_percent)) 
    blynk.virtual_write(6, str(last_time_watered))
    blynk.virtual_write(7, str(online_since))
    blynk.virtual_write(8, str(waterlevel_cm))

def read_moisture_sensor():                                         #reads the moisture value, adding the value to a list and takes average of that list
    #print("read moisture")
    global moisture_value_list
    global moisture_value
    if len(moisture_value_list) > 60:
        moisture_value_list.pop(0)
    moisture_value_list.append(round(adc.read_adc(A0), 2))
    moisture_value = round(get_average(moisture_value_list),0)
    
def read_light_sensor():                                            #reads the light value, adding the value to a list and takes average of that list
    global light_value_list
    global light_value
    if len(light_value_list) > 60:
        light_value_list.pop(0)
    light_value_list.append(round(adc.read_adc(A1),2))
    light_value = round(get_average(light_value_list),0)
    light_value = 1023-light_value
    
def waterlevel_cm_recognition():                                    #detects if the waterlevel_cm in the tank is low, sends an alert email
    global waterlevel_cm
    global LastMailReminder
    timedelta_mail_reminder = timedelta(minutes=60)

    if waterlevel_cm <= refill_reminder_height:
        if LastMailReminder  + timedelta_mail_reminder < datetime.now():
            send_email_notification()

def measure_distance():
    global waterlevel_cm
    global watertank_height
    TRIG = 23
    ECHO = 14

    GPIO.setup(TRIG,GPIO.OUT)
    GPIO.setup(ECHO,GPIO.IN)
     
    GPIO.output(TRIG, False)
    time.sleep(1)
     
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)
     
    while GPIO.input(ECHO)==0:
      pulse_start = time.time()
     
    while GPIO.input(ECHO)==1:
      pulse_end = time.time()
     
    pulse_duration = pulse_end - pulse_start
     
    distance = pulse_duration * 17150
     
    distance = round(distance, 2)
    #print("wrong: ", distance)
    if distance < watertank_height:
        print("Distance:",distance,"cm")
        waterlevel_cm = round(watertank_height - distance,2)
    else:
        measure_distance()
    #GPIO.cleanup()

def measure_distance_thread():
    while True:
        measure_distance()
        time.sleep(0.05)

def send_email_notification():                                      #sends an email to a certain adres
    global LastMailReminder
    with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()

        smtp.login(Email_user, Email_pw)

        subject = "SmartPlant Alert - Water tank is on low level"
        body = "Your water tank runs out of water. Refill soon!"

        msg = f'Subject: {subject}\n\n{body}'

        smtp.sendmail(adressee, Email_user, msg)
        LastMailReminder = datetime.now()

def thread_functions():
    while True:
        waterlevel_cm_recognition()
        read_moisture_sensor()
        read_light_sensor()
        send_values_to_blynk()
        time.sleep(.5)

time.sleep(5)

print("system running")

mosfet_output.start(0) 

second_thread = Thread(target= thread_functions)
second_thread.start()

third_thread = Thread(target = measure_distance_thread)
third_thread.start()

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
