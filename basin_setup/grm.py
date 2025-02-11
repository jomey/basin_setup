#!/usr/bin/env python3

import argparse
import datetime
import logging
import os
import shutil
import time
from subprocess import check_output

import coloredlogs
import netCDF4 as nc
import numpy as np
import pandas as pd
from inicheck.utilities import mk_lst, remove_chars
from spatialnc.topo import get_topo_stats
from spatialnc.utilities import copy_nc, mask_nc

from basin_setup import __version__

DEBUG = False


def parse_fname_date(fname):
    """
    Attempts to parse the date from the filename using underscores. This
    assumes there is a parseable date that can be found between underscores
    and can be determined with only numeric characters. E.g. 20200414. An
    example of this is in a file would be:

     USCASJ20200414_SUPERsnow_depth_50p0m_agg.tif

     Args:
        fname: File name containing a completely numeric date string in it

    Return:
        dt: Datetime object if pareable string was found, otherwise none
    """

    bname = os.path.basename(fname)

    # Remove the extension
    bname = bname.split('.')[0]

    dt = None

    if "_" in bname:

        # Assum underscores act like spaces
        bname = bname.split("_")

    else:
        bname = [bname]

    # Attempt to parse a date in the filename one at a time
    for w in bname:
        # Grab only numbers and letters
        dt_str = "".join([c for c in w if c.isnumeric()])

        try:
            # Successful datestring found, break out
            if dt_str:
                dt = pd.to_datetime(dt_str)
                break

        except BaseException:
            pass

    return dt


def parse_gdalinfo(fname):
    """
    Executes gdalinfo from the commandline on fname. Returns a dictionary of
    cell size, origin, and extents
    """

    image_info = {}
    info = check_output(["gdalinfo", fname], universal_newlines=True)
    for line in info.split("\n"):
        if "=" in line:
            data = line.split("=")
            data = [k.strip().lower() for k in data]

            # Keyword should always be first
            if data[0] in ["pixel size", "origin"]:

                result = []

                if "," in data[1]:
                    v = data[1].split(',')
                else:
                    v = [data[1]]

                for s in v:
                    result.append(float(remove_chars(s, "()\n:")))

                image_info[data[0]] = mk_lst(result, unlst=True)

    return image_info


class GRM(object):

    def __init__(self, **kwargs):

        # check kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)

        # Setup external logging if need be
        if not hasattr(self, 'log'):
            self.log = logging.getLogger(__name__)

        # Manage Logging
        level = "INFO"

        if hasattr(self, 'debug'):
            if self.debug:
                level = 'DEBUG'
            else:
                self.debug = False

        # Assign some colors and formats
        coloredlogs.install(fmt='%(levelname)-5s %(message)s', level=level,
                            logger=self.log)
        self.log.info("Getting Topo attributes...")
        self.ts = get_topo_stats(self.topo)
        self.log.info("Using topo cell size which is {} {}"
                      "".format(
                          abs(int(self.ts['du'])),
                          self.ts['units']))
        # Get aso super depth info
        self.image_info = parse_gdalinfo(self.image)

        # Titling
        renames = {"brb": "boise river basin",
                   "lakes": "mammoth lakes basin"}

        if self.basin in renames.keys():
            self.basin = renames[self.basin]
        else:
            self.basin = "{} river basin".format(self.basin)
        self.basin = self.basin.title()

        self.log.info("Working on the {}".format(self.basin))

    def handle_error(self, dbgmsg, errmsg, error=False):
        """
        Manages the error produced by our checks.
        """
        if error:
            self.log.error(errmsg)
            raise ValueError(errmsg)
        else:
            self.log.debug(dbgmsg)

    def grid_match(self):
        """
        Interpolates the newly scaled grid to the current grid
        """

        outfile = os.path.basename(self.image)

        self.log.info("Rescaling image raster from {} to {}"
                      "".format(int(self.image_info['pixel size'][0]),
                                abs(int(self.ts['du']))))

        outfile, ext = outfile.split(".")
        outfile = outfile + ".nc"

        outfile = os.path.join(self.temp, outfile)

        self.log.debug("Writing grid adjusted image to:\n{}".format(outfile))
        cmd = ["gdalwarp",
               "-r {}".format(self.resample),
               "-of NETCDF",
               "-overwrite",
               "-srcnodata -9999",
               "-dstnodata -9999",
               "-te {} {} {} {}".format(int(np.min(self.ts["x"])),
                                        int(np.min(self.ts["y"])),
                                        int(np.max(self.ts["x"])),
                                        int(np.max(self.ts["y"]))),
               "-ts {} {}".format(self.ts['nx'], self.ts['ny']),
               self.image,
               outfile]

        self.log.debug("Executing: {}".format(" ".join(cmd)))
        check_output(" ".join(cmd), shell=True)

        self.working_file = outfile

    def create_lidar_netcdf(self):
        """
        Creates a new lidar netcdf to contain all the flights for one
        water year.
        """

        self.log.info("Output NetCDF does not exist, creating a new one!")

        # Exclude all variables except dimensions
        ex_var = [v for v in self.topo_ds.variables if v.lower() not in [
            'x', 'y', 'projection']]
        # Copy a topo like netcdf image to add depths to
        self.ds = copy_nc(self.topo, self.outfile, exclude=ex_var)
        self.ds.createDimension("time", None)

        start_date = pd.to_datetime("{}-10-01".format(self.start_yr))
        self.log.debug("Using {} as start of water year for stamping netcdf"
                       "".format(start_date.isoformat()))

        # Assign time and count days since 10-1
        self.ds.createVariable('time', 'f', ('time'))
        setattr(
            self.ds.variables['time'],
            'units',
            'hours since %s' %
            start_date)
        setattr(self.ds.variables['time'], 'calendar', 'standard')

        # Add append a new image
        self.ds.createVariable("depth", "f", ("time", "y", "x"),
                                        chunksizes=(6, 10, 10),
                                        fill_value=np.nan)

        self.ds['depth'].setncatts({
            "units": "meters",
            "long_name": "lidar sself.now depths",
            "short_name": 'depth',
            "grid_mapping": "projection",
            "description": "Measured snow depth from ASO"
            " lidar."
        })

        # Adjust global attributes
        self.ds.setncatts({
            "last_modified": self.now,
            "dateCreated": self.now,
            "Title": "ASO 50m Lidar Flights Over the {} for Water Year {}."
            "".format(self.basin, self.water_year),
            "history": "Created using Basin Setup v{}"
            "".format(__version__),
        })

        # Attribute gets copied over from the topo
        # self.ds.delncattr("generation_command")

    def add_to_collection(self):
        """
        Adds a new netcdf to an existing one that includes all the metadata.
        If the existing file doesn't actually exist, it will create one.
        """

        # Grab a human readable timefor today
        self.now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not hasattr(self, "date"):
            self.date = parse_fname_date(self.image)

            # No date was parsed
            if self.date is None:
                msg = ('Unable to parse date from filename {}.'
                       ''.format(self.image))
                self.log.error('{}, Please use the --date flag to manually '
                               'enforce the date'.format(msg))
                raise Exception(msg)

        else:
            self.date = pd.to_datetime(self.date)

        # Calculate the start of the water year and the water year
        self.water_year = self.date.year

        if self.date.month <= 10:
            self.start_yr = self.water_year - 1
        else:
            self.water_year += 1

        # output netcdf
        self.outfile = os.path.join(self.output, ("lidar_depths_wy{}.nc"
                                                  "".format(self.water_year)))
        self.log.info("Lidar Flight for {}".format(
            self.date.isoformat().split('T')[0]))

        if not hasattr(self, "epsg"):
            pass

        # Open the topo for gathering data from
        self.topo_ds = nc.Dataset(self.topo, mode='r')

        # Prexisting collection of lidar in netcdf found
        if os.path.isfile(self.outfile):
            self.log.info(
                "Output NetCDF exists, checking to see if everything matches.")

            # Retrieve existing dataset
            self.ds = nc.Dataset(self.outfile, mode='a')

            # Check for matching basins
            self.check_basin_match()

            # Check the incoming topo matches the previous images domain
            self.check_domain_match()

            # Check for matching water years
            self.check_water_year_match()

            # Check to see if we are about to overwrite data
            self.check_overwrite()

            # Check the basin mask in the topo matches for the dataset
            self.check_topo_basin_name()

        # Create a netcdf
        else:
            self.create_lidar_netcdf()

        # Calculate the time index
        index = self.get_time_index()

        # Update the modified attribute
        self.ds.setncatts({"last_modified": self.now})

        # Open the newly convert depth and add it to the collection
        self.log.info("Extracting the new netcdf data...")
        new_ds = nc.Dataset(self.working_file, mode='a')

        # Fill values to np.nan
        idx = new_ds.variables['Band1'][:] == \
            new_ds.variables['Band1']._FillValue
        new_ds.variables['Band1'][:][idx] = np.nan

        new_ds.variables['Band1'][:] = np.flipud(new_ds.variables['Band1'][:])

        new_ds.close()

        # Mask it
        self.log.info("Masking lidar data...")
        new_ds = mask_nc(self.working_file, self.topo, output=self.temp)

        # Save it to output
        self.log.info(
            "Adding masked lidar data to {}".format(
                self.ds.filepath()))

        self.ds.variables['depth'][index, :] = new_ds.variables['Band1'][:]
        self.ds.sync()

        self.ds.close()
        new_ds.close()
        self.topo_ds.close()

    def get_time_index(self):
        """
        Calculates the time based index in hours for current image to go into
        the existing netcdf for lidar depths
        """
        # Get the timestep in hours. Set all images to 2300
        self.log.debug("Calculating the time index...")

        times = self.ds.variables['time']

        t = nc.date2num(self.date + pd.to_timedelta(23, unit='h'),
                        times.units,
                        times.calendar)

        # Figure out the time index
        if len(times) != 0:
            index = np.where(times[:] == t)[0]

            if index.size == 0:
                index = len(times)
            else:
                index = index[0]
        else:
            index = len(times)

        self.log.info("Input data is {} hours from the beginning of the water"
                      " year.".format(int(t)))

        self.ds.variables['time'][index] = t

        return index

    def check_topo_basin_name(self):
        """
        Checks to see if the topo masks long name matches the basin name of the
        image
        """

        topo_mask = self.topo_ds.variables['mask'].long_name.lower()

        # Flexible naming convention in the topo to check for matches
        keywords = [w for w in topo_mask.split(" ") if w not in [
            'river', 'basin']]

        for key in keywords:
            if key in self.basin.lower():
                found = True
                break
            else:
                found = False

        self.handle_error("Topo's mask name matches the basin name.",
                          ("Topo's mask ({}) is not associated to the {}."
                           "".format(topo_mask, self.basin)),
                          error=not found)

    def check_water_year_match(self):
        """
        Checks if the the current images date and the existing lidar depths
        are in the same water year.
        """
        time_units = self.ds.variables['time'].units

        # Calculate the WY from the time units
        nc_wy = pd.to_datetime(time_units.split("since")[-1]).year + 1

        error = int(nc_wy) != self.water_year

        dbgmsg = "Input image water year matches prexisting netcdf's"
        errmsg = ("Attempting to add an image apart of water year {} "
                  " to an existing lidar depths netcdf for water year {}"
                  "".format(self.water_year, nc_wy))

        self.handle_error(dbgmsg, errmsg, error=error)

    def check_basin_match(self):
        """
        Checks that the basin name provided matches whats in the existing
        netcdf
        """
        error = not self.basin.lower() in self.ds.getncattr("Title").lower()

        dbgmsg = "Basin entered matches the basin in the preexisting file."
        errmsg = ("The preexisting lidar depths file has a title of {} which"
                  " should contain {} to add this image."
                  "".format(self.ds.getncattr("Title"), self.basin))

        self.handle_error(dbgmsg, errmsg, error=error)

    def check_overwrite(self):
        """
        Checks that the netcdf doesn't already contain this date for a flight
        """

        times = self.ds.variables['time']
        ncdates = nc.num2date(times[:], times.units, calendar=times.calendar)
        ncdates = np.array([pd.to_datetime(dt).date() for dt in ncdates])

        # Is the incoming date already in the file?
        error = self.date.date() in ncdates
        errmsg = ("This image's date is already in the preexisting netcdf.")
        dbgmsg = ("Incoming date appears to be unique to the dataset.")
        self.handle_error(dbgmsg, errmsg, error=error)

    def check_domain_match(self):
        """
        Checks that the topo coming in matches the domain of the current
        netcdf being modified
        """
        errmsg = ("This lidar file was not initially created with"
                  " this topo. Domain mismatch. Either delete the existing"
                  " lidar netcdf you adding to, or find the correct topo.")
        error = False
        dbgmsg = 'Topo domain and resolution matches the current lidar netCDF!'

        # Check that domain extents are the same
        for v in ['x', 'y']:
            for fn in [np.max, np.min]:
                v_topo = fn(self.topo_ds.variables[v][:])
                v_lidar = fn(self.ds.variables[v][:])

                if v_topo != v_lidar:
                    error = True
                    dbgmsg = ("ERROR: Domain mismatch, Topo {0} {1} != Lidar NetCDF {0} {1}"  # noqa
                              "".format(v, fn.__name__))

            # Check that the topo and the current lidar netcdf have the same
            # nx,ny
            if len(self.topo_ds.variables[v][:]) != len(
                    self.ds.variables[v][:]):
                error = True
                dbgmsg = ("ERROR Domain Mismatch: Topo n{0} != Lidar NetCDF n{0}"  # noqa
                          "".format(v))

        self.handle_error(dbgmsg, errmsg, error=error)


def run_grm(**kwargs):
    '''
    Run GRM for one image
    '''
    g = GRM(**kwargs)
    g.grid_match()
    g.add_to_collection()
    # return g


def main():

    p = argparse.ArgumentParser(description="Modifies existing images to a"
                                            " different scale and shifts the"
                                            " grid to match modeling domains"
                                            " for SMRF/AWSM")

    p.add_argument("-t", "--topo", dest="topo",
                   required=True,
                   help="Path to the topo.nc file used for modeling")

    p.add_argument("-i", "--images", dest="images",
                   required=True, nargs='+',
                   help="Path(s) to lidar images for processing")

    p.add_argument("-b", "--basin", dest="basin",
                   required=True, choices=['brb', 'kaweah', 'kings', 'lakes',
                                           'merced', 'sanjoaquin', 'tuolumne'],
                   help="Name of the basin to use for metadata")

    p.add_argument("-o", "--output", dest="output",
                   required=False, default='output',
                   help="Path to output folder")

    p.add_argument("-d", "--debug", dest="debug",
                   required=False, action='store_true',
                   help="Outputs more information and does not delete any"
                         " working files generated during runs")

    p.add_argument("-dt", "--dates", dest="dates",
                   required=False, default=[], nargs='+',
                   help="Enables user to directly control the date(s). Should "
                        " be a list as long as the images argument. If left"
                        " empty GRM will attempt to find the date in the file"
                        " name of the image")

    p.add_argument("-e", "--allow_exceptions", dest="allow_exceptions",
                   required=False, action="store_true",
                   help="For Development purposes, allows it to be debugging"
                   " but also enables the errors to NOT catch, which is useful"
                   " for batch processing.")

    p.add_argument("-r", "--resample", dest="resample",
                   choices=['near', 'bilinear', 'cubic', 'cubicspline',
                            'lanczos', 'average', 'mode', 'max', 'min',
                            'med', 'Q1', 'Q3'],
                   required=False, default="bilinear",
                   help="Pass through the resample technique to use in"
                         " gdalwarp .")

    args = p.parse_args()

    # Global debug variable
    global DEBUG
    DEBUG = args.debug

    start = time.time()
    skips = 0

    # Make sure our output folder exists
    output = args.output

    # Make the output folder
    if not os.path.isdir(output):
        os.mkdir(output)

    # Make the temp folder inside the output folder
    temp = os.path.join(output, 'tmp')
    if not os.path.isdir(temp):
        os.mkdir(temp)

    if not isinstance(args.images, list):
        args.images = [args.images]

    # Get logger and add color with a simple format
    log = logging.getLogger(__name__)
    coloredlogs.install(fmt='%(levelname)-5s %(message)s', level="INFO",
                        logger=log)
    # Print a nice header with version number
    msg = "\n\nGrid Resizing and Matching Script v{}".format(__version__)
    header = "=" * (len(msg) + 1)
    log.info(msg + "\n" + header + "\n")

    # We need to sort the images by date so create a dictionary of the two here
    log.info("Calculating dates and sorting images for processing...")
    if args.dates:
        dates = args.dates

    else:
        dates = [parse_fname_date(f) for f in args.images]

    # Confirm there as many dates as images
    if len(dates) != len(args.images):
        raise Exception("Provided dates must either be in the image filename"
                        " in the format YYYYMMDD or provided using --date."
                        " If using the date flag, there must be as many dates"
                        " as images.")

    image_dict = {k: v for (k, v) in zip(dates, args.images)}

    # Loop through all images provided
    log.info("Number of images being processed: {}".format(len(args.images)))

    for d in sorted(image_dict.keys()):
        f = image_dict[d]

        log.info("")
        log.info("Processing {}".format(os.path.basename(f)))

        kwargs = {'image': f, 'topo': args.topo, 'basin': args.basin,
                  'debug': args.debug,
                  'output': output,
                  'temp': temp,
                  'resample': args.resample,
                  'date': d,
                  'log': log}

        if not DEBUG or args.allow_exceptions:
            try:
                run_grm(**kwargs)

            except Exception as e:
                log.warning("Skipping {} due to error".format(
                    os.path.basename(f)))
                log.error(e)
                skips += 1

        else:
            run_grm(**kwargs)

    stop = time.time()

    # Throw a warning when all get skipped
    if skips == len(args.images):
        log.warning("No images were processed!")

    log.info("Grid Resizing and Matching Complete. {1}/{2} files processed."
             " Elapsed Time {0:0.1f}s"
             "".format(
                 stop - start,
                 len(args.images) - skips,
                 len(args.images)
             ))

    if not DEBUG:
        log.info('Cleaning up temporary files.')
        shutil.rmtree(temp)


if __name__ == '__main__':
    main()
