import sys
sys.path.append("C:/development/Python/pyall")

import os.path
from datetime import datetime
import geodetic
import numpy as np
import time
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from PIL import ImageOps
from PIL import ImageChops
import math
import shadedRelief as sr
from matplotlib import pyplot as plt
import pyall
from glob import glob
import argparse
import matplotlib.cm as cm
import csv

def main():
    parser = argparse.ArgumentParser(description='Read Kongsberg ALL file and create a hill shaded color waterfall image.')
    parser.add_argument('-i', dest='inputFile', action='store', help='-i <ALLfilename> : input ALL filename to image. It can also be a wildcard, e.g. *.all')
    parser.add_argument('-s', dest='shadeScale', default = 1.0, action='store', help='-s <value> : Shade scale factor. a smaller number (0.1) provides less shade that a larger number (10) Range is anything.  [Default - 1.0]')
    parser.add_argument('-r', action='store_true', default=False, dest='rotate', help='-r : Rotate the resulting waterfall so the image reads from left to right instead of bottom to top.  [Default is bottom to top]')
    parser.add_argument('-gray', action='store_true', default=False, dest='gray', help='-gray : Apply a gray scale depth palette to the image instead of a color depth.  [Default is False]')

    if len(sys.argv)==1:
        parser.print_help()
        sys.exit(1)
    
    #load a nice color palette
    colors = loadPal(os.path.dirname(os.path.realpath(__file__)) + '/jeca.pal')
    args = parser.parse_args()

    print ("processing with settings: ", args)
    # print ("Files to Process:", glob(args.inputFile))
    for filename in glob(args.inputFile):
        xResolution, yResolution, beamCount, leftExtent, rightExtent, navigation = computeXYResolution(filename)
        print("XRes %.2f YRes %.2f beamCount %d leftExtent %.2f, rightExtent %.2f" % (xResolution, yResolution, beamCount, leftExtent, rightExtent)) 
        
        if beamCount == 0:
            print ("No data to process, skipping empty file")
            continue
        createWaterfall(filename, colors, beamCount, float(args.shadeScale), xResolution, yResolution, args.rotate, args.gray, leftExtent, rightExtent, navigation)

def computeXYResolution(fileName):    
    '''compute the approximate across and alongtrack resolution so we can make a nearly isometric Image'''
    '''we compute the across track by taking the average Dx value between beams'''
    '''we compute the alongtracks by computing the linear length between all nav updates and dividing this by the number of pings'''
    xResolution = 1
    YResolution = 1
    prevLong = 0 
    prevLat = 0
    r = pyall.ALLReader(fileName)
    recCount = 0
    acrossMeans = np.array([])
    alongIntervals = np.array([])
    leftExtents = np.array([])
    rightExtents = np.array([])
    beamCount = 0
    distanceTravelled = 0.0
    navigation = []

    while r.moreData():
        TypeOfDatagram, datagram = r.readDatagram()
        if (TypeOfDatagram == 'P'):
            datagram.read()
            if prevLat == 0:
                prevLat =  datagram.Latitude
                prevLong =  datagram.Longitude
            range,bearing1, bearing2  = geodetic.calculateRangeBearingFromGeographicals(prevLong, prevLat, datagram.Longitude, datagram.Latitude)
            distanceTravelled += range
            navigation.append([recCount, r.currentRecordDateTime(), datagram.Latitude, datagram.Longitude])
            prevLat =  datagram.Latitude
            prevLong =  datagram.Longitude
        if (TypeOfDatagram == 'X') or (TypeOfDatagram == 'D'):
            datagram.read()
            if datagram.NBeams > 1:
                acrossMeans = np.append(acrossMeans, np.average(np.diff(np.asarray(datagram.AcrossTrackDistance))))
                leftExtents = np.append(leftExtents, datagram.AcrossTrackDistance[0])
                rightExtents = np.append(rightExtents, datagram.AcrossTrackDistance[-1])
                recCount = recCount + 1
                beamCount = max(beamCount, len(datagram.Depth)) 
            
        #limit to a few records so it is fast
        # if recCount == 100:
        #     break
    r.close()
    if recCount == 0:
        return 0,0,0,0,0,[] 
    xResolution = np.average(acrossMeans)
    yResolution = distanceTravelled / recCount
    return xResolution, yResolution, beamCount, np.min(leftExtents), np.max(rightExtents), navigation

def createWaterfall(filename, colors, beamCount, shadeScale=1, xResolution=1, yResolution=1, rotate=False, gray=False, leftExtent=-100, rightExtent=100, navigation = []):
    print ("Processing file: ", filename)

    r = pyall.ALLReader(filename)
    totalrecords = r.getRecordCount()
    start_time = time.time() # time the process
    recCount = 0
    imageZoom = 4
    waterfall = []
    outputResolution = beamCount * imageZoom
    isoStretchFactor = (yResolution/xResolution) * imageZoom
    # print ("xRes %.2f yRes %.2f AcrossStretch %.2f" % (xResolution, yResolution, isoStretchFactor))
    while r.moreData():
        TypeOfDatagram, datagram = r.readDatagram()
        if (TypeOfDatagram == 0):
            continue
        if (TypeOfDatagram == 'X') or (TypeOfDatagram == 'D'):
            datagram.read()
            if datagram.NBeams == 0:
                continue

            # if datagram.SerialNumber == 275:                    
            for d in range(len(datagram.Depth)):
                datagram.Depth[d] = datagram.Depth[d] + datagram.TransducerDepth

            # we need to stretch the data to make it isometric, so lets use numpy interp routing to do that for Us
            xp = np.array(datagram.AcrossTrackDistance) #the x distance for the beams of a ping.  we could possibly use teh real values here instead todo
            fp = np.array(datagram.Depth) #the depth list as a numpy array
            x = np.linspace(leftExtent, rightExtent, outputResolution) #the required samples needs to be about the same as the original number of samples, spread across the across track range
            newDepths = np.interp(x, xp, fp, left=0.0, right=0.0)
            waterfall.insert(0, np.asarray(newDepths))            

        recCount += 1
        if r.currentRecordDateTime().timestamp() % 30 == 0:
            percentageRead = (recCount / totalrecords) 
            update_progress("Decoding .all file", percentageRead)
    update_progress("Decoding .all file", 1)

    # we now need to interpolate in the along track direction so we have apprximate isometry
    npGrid = np.array(waterfall)
    npGrid = np.ma.masked_values(npGrid, 0.0)

    stretchedGrid = np.empty((0, int(len(npGrid) * isoStretchFactor)))    
    for column in npGrid.T:
        y = np.linspace(0, len(column), len(column) * isoStretchFactor) #the required samples
        yp = np.arange(len(column)) 
        w2 = np.interp(y, yp, column, left=0.0, right=0.0)
        # w2 = np.interp(y, yp, column, left=None, right=None)
        stretchedGrid = np.append(stretchedGrid, [w2],axis=0)
    npGrid = stretchedGrid
    npGrid = np.ma.masked_values(npGrid, 0.0)
    # meanDepth = np.average(waterfall)
    # print ("Mean Depth %.2f" % meanDepth)
    if gray:
        #Create hillshade a little brighter
        npGrid = npGrid.T * -1.0 * shadeScale
        hs = sr.calcHillshade(npGrid, 1, 45, 30)
        img = Image.fromarray(hs).convert('RGBA')
    else:
        npGrid = npGrid.T * shadeScale
        #Create hillshade a little darker as we are blending it
        hs = sr.calcHillshade(npGrid, 1, 45, 5)
        img = Image.fromarray(hs).convert('RGBA')
        # calculate color height map
        cmrgb = cm.colors.ListedColormap(colors, name='from_list', N=None)
        m = cm.ScalarMappable(cmap=cmrgb)
        colorArray = m.to_rgba(npGrid, alpha=None, bytes=True)    
        colorImage = Image.frombuffer('RGBA', (colorArray.shape[1], colorArray.shape[0]), colorArray, 'raw', 'RGBA', 0,1)
        # now blend the two images
        img = ImageChops.subtract(colorImage, img).convert('RGB')

    #rotate the image if the user requests this.  It is a little better for viewing in a browser
    if rotate:
        img = img.rotate(-90, expand=True)
    annotateWaterfall(img, navigation, isoStretchFactor)
    img.save(os.path.splitext(filename)[0]+'.png')
    print ("Saved to: ", os.path.splitext(filename)[0]+'.png')

    r.rewind()
    print("Complete converting ALL file to waterfall :-)")
    r.close()    

def annotateWaterfall(img, navigation, scaleFactor):
    '''loop through the navigation and annotate'''
    lastTime = 0.0 
    lastRecord = 0
    for record, date, lat, long in navigation:
        if (record % 100 == 0) and (record != lastRecord):
            writeLabel(img, int(record * scaleFactor), str(date.strftime("%H:%M:%S")))
            lastRecord = record
    return img

def writeLabel(img, x, label):
    y = 0
    f = ImageFont.truetype("arial.ttf",size=16)
    txt=Image.new('L', (500,50))
    d = ImageDraw.Draw(txt)
    d.text( (0, 0), label,  font=f, fill=255)
    d.line((0, 0, 20, 0), fill=255)
    w=txt.rotate(-90,  expand=1)
    img.paste( ImageOps.colorize(w, (0,0,0), (0,0,255)), (x, y),  w)
    return img

def update_progress(job_title, progress):
    length = 20 # modify this to change the length
    block = int(round(length*progress))
    msg = "\r{0}: [{1}] {2}%".format(job_title, "#"*block + "-"*(length-block), round(progress*100, 2))
    if progress >= 1: msg += " DONE\r\n"
    sys.stdout.write(msg)
    sys.stdout.flush()


def loadPal(paletteFileName):
    '''this will load and return a .pal file so we can apply colors to depths.  It will strip off the headers from the file and return a list of n*RGB values'''
    colors = []
    with open(paletteFileName,'r') as f:
        next(f) # skip headings
        next(f) # skip headings
        next(f) # skip headings
        reader=csv.reader(f,delimiter='\t')
        for red,green,blue in reader:
            thiscolor = [float(red)/255.0, float(green) / 255.0, float(blue) / 255.0]
            colors.append(thiscolor)
    return colors

def loadNavigation(fileName):    
    '''loads all the navigation into lists'''
    navigation = []
    r = pyall.ALLReader(fileName)
    while r.moreData():
        TypeOfDatagram, datagram = r.readDatagram()
        if (TypeOfDatagram == 'P'):
            datagram.read()
            navigation.append([datagram.Time, datagram.Latitude, datagram.Longitude])
    r.close()
    return navigation

if __name__ == "__main__":
    main()

