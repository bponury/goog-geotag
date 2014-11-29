#!/usr/bin/python
#
# Author: Bartosz [ponury] Ponurkiewicz
#
# Download your location history from
# https://maps.google.com/locationhistory/ (export as KML file)
#
# Usage:
# Example #1:
# goog-geotag.py -f location.kml -s -d 2000 -t 3600 -c location *.jpg
#   Using location.kml file as location history process all jpg
#   files from current dir and save found GPS coordinates in exif
#   if the distance approximation is not bigger than 2000 meters
#   or if the time of the photo is less than 1h from history entry
#
# Example #2:
# goog-geotag.py -f location.cache -s -F -o 60 *.jpg
#   Using location.cache file as history process all jpg files
#   from current dir and save GPS coords in exif even if file
#   already contains GPS coords. Shift photo time 60 minutes into future

import sys
import xml.etree.ElementTree as ET
import getopt
import time
from datetime import datetime
import dateutil.parser
import calendar
from collections import OrderedDict
import numpy
import math
from gi.repository import GExiv2
import fractions
import glob
import pickle

verbose = False

HEADER = '\033[95m'
OKBLUE = '\033[94m'
OKGREEN = '\033[92m'
WARNING = '\033[93m'
FAIL = '\033[91m'
ENDC = '\033[0m'
BOLD = "\033[1m"

def Info(str):
	print OKGREEN + '[+] %s' % str + ENDC

def Error(str):
	print WARNING + '[-] %s' % str + ENDC

def Fail(str):
	print BOLD + FAIL + '[!] %s' % str + ENDC

def Debug(str):
	global verbose
	if verbose:
		print '\t%s' % str

def GetHistoryListFromRoot(root):
	placemark = root.find('.//{http://www.google.com/kml/ext/2.2}Track')
	history = OrderedDict()
	timestamp = None

	for child in placemark:
		tag = child.tag[child.tag.index('}')+1:]
		if tag == 'when':
			timestamp = int(WhenToTimestamp(child.text))
			#print '%s => %s %s' % (child.text, timestamp, TimestampToHRDate(timestamp))
		if tag == 'coord':
			history[timestamp] = child.text

	return history

def WhenToTimestamp(when):
	return calendar.timegm(dateutil.parser.parse(when).utctimetuple())

def TimestampToHRDate(timestamp):
	return datetime.fromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M:%S')

def MapsLinkFromPosition(latitude, longitude):
	return 'http://maps.google.com/maps?q=%f,%f+(coord+point)&z=14&ll=%f,%f' % (latitude, longitude, latitude, longitude)

def Lerp(begin, end, percent):
	return (begin + percent*(numpy.subtract(end, begin)))

def GetDistance(lat1, long1, lat2, long2):
	degrees_to_radians = math.pi/180.0

	phi1 = (90.0 - lat1)*degrees_to_radians
	phi2 = (90.0 - lat2)*degrees_to_radians

	theta1 = long1*degrees_to_radians
	theta2 = long2*degrees_to_radians

	cos = (math.sin(phi1)*math.sin(phi2)*math.cos(theta1 - theta2) +
			math.cos(phi1)*math.cos(phi2))
	arc = math.acos( cos )

	return arc * 6378100

def Fraction(n):
	return fractions.Fraction.from_float(n).limit_denominator(99999).__str__()

def GetCoordFromDecimal(decimal):
	remainder, degrees = math.modf(abs(decimal))
	remainder, minutes = math.modf(remainder * 60)
	return ' '.join([Fraction(n) for n in (degrees, minutes, remainder * 60)])

def GetPositionFromTimestamp(history, timestamp):
	before = None
	after = None
	time_diff = None
	history_asc = True

	if history.items()[0][0] > history.items()[1][0]:
		history_asc = False

	timestamp = int(timestamp)
	for when, where in history.iteritems():
		if int(when) <= int(timestamp):
			before = {}
			before['when'] = when
			before['N'] = float(where.split(' ')[1])
			before['W'] = float(where.split(' ')[0])
			before['A'] = float(where.split(' ')[2])
			if not history_asc:
				break
		if int(when) >= int(timestamp):
			after = {}
			after['when'] = when
			after['N'] = float(where.split(' ')[1])
			after['W'] = float(where.split(' ')[0])
			after['A'] = float(where.split(' ')[2])
			if history_asc:
				break

	if not before or not after:
		return None

	time_diff = after['when'] - before['when']
	distance = GetDistance(before['N'], before['W'], after['N'], after['W'])
	if after['when'] - before['when'] == 0:
		percent = 0
	else:
		percent = (float(timestamp) - before['when']) / (after['when'] - before['when'])

	Debug('timestamp: %s (%s)' % (timestamp, TimestampToHRDate(timestamp)))
	Debug('start: %s (%s)' % (before, TimestampToHRDate(before['when'])))
	Debug('\t%s' % MapsLinkFromPosition(before['N'], before['W']))
	Debug('stop : %s (%s)' % (after, TimestampToHRDate(after['when'])))
	Debug('\t%s' % MapsLinkFromPosition(after['N'], after['W']))
	Debug('time diff : %d seconds (%d min)' % (time_diff, time_diff / 60))
	Debug('distance: %d meters (%.2fkm)' % (distance, distance/1000))
	Debug('route percentage: %.2f%%' % (percent*100))

	begin = numpy.array([before['when'], before['W'], before['N']])
	end = numpy.array([after['when'], after['W'], after['N']])
	position = Lerp(begin, end, percent)
	altitude = 0
	print '\t' + OKBLUE + MapsLinkFromPosition(position[2], position[1]) + ENDC + '\n'

	return {
			'latitude': position[2],
			'longitude': position[1],
			'altitude': altitude,
			'distance_diff': distance,
			'time_diff': min(timestamp - before['when'], after['when'] - timestamp),
			'percentage': percent
	}

def usage():
	print '%s -f <location_history.kml> [OPTIONS] <img#1> [img#2] ...' % sys.argv[0]
	print 'Ex: %s -f location.kml -s -d 2000 -t 3600 *.jpg\n' % sys.argv[0]
	print 'OPTIONS:'
	print '\t-f <file>      - KML or cache file with location history'
	print '\t-o <offset>    - Time offset in minutes'
	print '\t-s             - Save GEO data inside exif'
	print '\t-d <distance>  - Distance fuzziness limit in meters'
	print '\t-t <time>      - Time fuzziness limit in seconds'
	print '\t-F             - Do NOT skip files with GPS info in exif'
	print '\t-c <file>      - Dump cache of location history list to <file>.cache'
	print '\t-v             - Print verbose messagess'

def main():
	try:
		opts, args = getopt.getopt(sys.argv[1:], 'hf:o:sd:t:Fc:v')
	except getopt.GetoptError as err:
		Fail(str(err))
		usage()
		sys.exit(2)

	filename = None
	timestamp = None
	time_offset = 0
	perform_save = None
	distance_fuzziness = None
	time_fuzziness = None
	force = False
	cache_history = None

	for o, a in opts:
		if o == '-f':
			filename = a
		elif o == '-o':
			time_offset = float(a)
		elif o == '-s':
			perform_save = True
		elif o == '-d':
			distance_fuzziness = int(a)
		elif o == '-t':
			time_fuzziness = int(a)
		elif o == '-F':
			force = True
		elif o == '-c':
			cache_history = a
		elif o == '-v':
			global verbose
			verbose = True
		else:
			usage()
			assert False, 'unhandled option'

	if not filename:
		usage()
		sys.exit(1)

	history = None
	if '.cache' in filename:
		Info('Loading history cache from file')
		with open(filename) as f:
			history = pickle.load(f)
	else:
		Info('Parsing KML file')
		tree = ET.parse(filename) or sys.exit('Unable to parse file!')
		root = tree.getroot()
		history = GetHistoryListFromRoot(root)

	if cache_history:
		with open(cache_history + '.cache', 'w') as f:
			pickle.dump(history, f)
		Info('Cache file generated')

	Debug('time offset: %d minutes' % time_offset)

	failed = []
	for image in args:
		exif = None
		try:
			exif = GExiv2.Metadata(image)
		except:
			Fail('Unable to read file: %s' % image)
			failed.append(image)
			continue

		if not force and exif and 'Exif.GPSInfo.GPSLatitude' in exif:
			Info('Skipping: %s (contains GPS info)' % image)
			continue

		Info('Processing: %s%s' % (BOLD, image))

		if not exif:
			sys.exit('Unable to fetch exif for file file: %s' % image)

		#for k in exif.get_tags():
			#print '%s => %s' % (k, exif[k])

		dto = exif['Exif.Photo.DateTimeOriginal']
		timestamp = int(time.mktime(datetime.strptime(dto, '%Y:%m:%d %H:%M:%S').timetuple()))
		timestamp += time_offset

		coord = GetPositionFromTimestamp(history, timestamp)
		if coord == None:
			Fail('No coord data in file %s for %s' % (filename, time.ctime(timestamp)))
			continue

		d_fail = t_fail = False
		if distance_fuzziness and coord['distance_diff'] > distance_fuzziness:
			Error('Distance difference too big (%d > %d)' % (coord['distance_diff'], distance_fuzziness))
			d_fail = True
		if time_fuzziness and coord['time_diff'] > time_fuzziness:
			Error('Time difference too big (%d > %d)' % (coord['time_diff'], time_fuzziness))
			t_fail = True

		if d_fail and t_fail:
			Fail('Distance and time too big, aborting')
			failed.append(image)
			continue

		if perform_save:
			exif.set_gps_info(coord['longitude'], coord['latitude'], coord['altitude'])
			exif.save_file()
			Info('Exif saved')

	if failed:
		Error('Failed images: %d' % len(failed))
	for i in failed:
		print '\t- %s' % i

	sys.exit(1 if failed else 0)


if __name__ == '__main__':
	main()
