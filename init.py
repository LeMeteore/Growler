#!/usr/bin/env python3.4
# init.py
#
# Initialize a new Growler application
#

from sys import (argv)
import os
import stat

if len(argv) < 2:
  print ("Please specify a destination")
  print ("usage : %s <project_directory>" % (argv[0]))
  exit()

dirname = argv[1]

if not os.path.exists(dirname):
  os.mkdir(dirname)
else:
  if os.path.isfile(dirname):
    print ("Provided name '%s' is a file. Aborting." % (dirname))
    exit()
  elif not os.path.isdir(dirname) or len(os.listdir(dirname)) != 0:
    print ("Provided name '%s' is a nonempty directory. Aborting." % (dirname))
    exit()

print ("Initializing directory '%s'" % (dirname))

config_filename = "config.ini"
run_filename = "run.py"
app_fileanme = "app.py"

app_classname = "%sApp" % (dirname)

os.chdir(dirname)

runfile = open(run_filename, "w")
runfile_contents = """#
# run.py
#

from configparser import ConfigParser
from app import {0}

conf = ConfigParser()
#conf.read_file(open('config.ini'))
conf.read('config.ini')

app = {0}(conf)

app.run()
""".format(app_classname)


runfile.write(runfile_contents)
runfile.close()

conffile = open(config_filename, "w")
conffile.write("""#
# Configuration for project {}
#

[server]
host=localhost
port=8080

""".format(dirname))
conffile.close()

appfile = open("app.py", "w")
appfile.write("""
from growler import App

class {0}(App):

  def __init__(self, config):
    super().__init__(__class__.__name__, settings = config)
    print ("Running {0}")
""".format(app_classname))
appfile.close()

os.chmod(config_filename, stat.S_IRUSR | stat.S_IWUSR)
os.chmod(run_filename, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IROTH)

print ("*** Finished initializing new growler project : %s" % (dirname))

