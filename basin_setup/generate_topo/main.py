import os

import logging
import xarray as xr

from basin_setup.utils.logger import BasinSetupLogger
from basin_setup.utils import config, domain_extent, gdal
from basin_setup import __version__
from basin_setup.generate_topo.shapefile import Shapefile
from basin_setup.generate_topo import vegetation


class GenerateTopo():

    def __init__(self, config_file):

        self.ucfg, self.configFile = config.read(config_file)
        self.config = self.ucfg.cfg['generate_topo']

        self.basin_setup_logger = BasinSetupLogger(self.ucfg.cfg['logging'])
        self._logger = logging.getLogger(__name__)

        self._logger.info("Generate Topo Tool -- v{0}".format(__version__))

        config.check(self.ucfg, self._logger)

        self.temp_dir = os.path.join(self.config['output_folder'], 'temp')
        self.cell_size = self.config['cell_size']

        self.images = {}

    def run(self):

        self.set_extents()
        self.load_basin_shapefiles()
        self.load_dem()
        self.load_vegetation()
        # self.create_netcdf()
        # self.calculate_k_and_tau()
        # self.add_project_to_topo()

        # # Add the projection information
        # topo = add_proj(
        #     topo,
        #     images['basin outline']['epsg'],
        #     images['dem']['path'])

        # # Don't Flip the image over the x axis if it is not requested
        # if not args.noflip:
        #     out.msg("Flipping images...")
        #     topo = flip_image(topo)

        # topo.close()

        # out.msg('\nRequested output written to:')
        # out.respond('{0}'.format(join(required_dirs['output'], 'topo.nc')))
        # out.msg("Basin setup files complete!\n")

        # # Don't Clean up if we are debugging so we can look at the steps
        # if not DEBUG:
        #     out.msg('Cleaning up temporary files.')
        #     rmtree(required_dirs['temp'])

    def set_extents(self):
        """Set the extents to clip the rasters to. This will either use
        the users values in `coordinate_extent` or calculate from the
        basin outline. If the extents are from the basin outline, the
        padding will be applied from `pad_domain`.
        """

        if self.config['coordinate_extent'] is None:
            extents, _ = domain_extent.parse_from_file(
                self.config['basin_shapefile'])

            padding = [self.cell_size * pad
                       for pad in self.config['pad_domain']]

            # Pad the extents
            extents[0] -= padding[0]  # Left
            extents[1] -= padding[1]  # Bottom
            extents[2] += padding[2]  # Right
            extents[3] += padding[3]  # Top

            # Check cell size fits evenly in extent range
            extents = domain_extent.condition_to_cellsize(
                extents, self.cell_size, self._logger)

        else:
            extents = self.config['coordinate_extent']

        self.extents = extents

        # Create an Affine transform and x/y vectors for the domain
        self.transform, self.x, self.y = domain_extent.affine_transform_from_extents(  # noqa
            extents, self.cell_size)

    def load_basin_shapefiles(self):
        """ Load the basin and sub basin shapefiles into `Shapefile` class
        """

        self._logger.info("Loading shapefiles...")
        self.basin_shapefiles = [
            Shapefile(self.config['basin_shapefile'])
        ]

        # The project CRS is based on the basin shapefile
        self.crs = self.basin_shapefiles[0].crs

        # Add the sub basin files
        if self.config['sub_basin_files'] is not None:
            for sub_basin_file in self.config['sub_basin_files']:
                self.basin_shapefiles += Shapefile(sub_basin_file)

    def load_dem(self):
        """Reproject and crop the DEM file to a new image
        """

        self.images['dem'] = os.path.join(self.temp_dir, 'clipped_dem.tif')

        gdal.gdalwarp(
            self.config['dem_file'],
            self.images['dem'],
            self.crs['init'],
            self.extents,
            self.cell_size,
            resample='bilinear',
            logger=self._logger
        )

    def load_vegetation(self):

        veg = None
        if self.config['vegetation_dataset'] == 'landfire_1.4.0':
            veg = vegetation.Landfire140()

            veg.reproject(self.extents, self.cell_size, self.crs['init'])
            veg.calculate_tau_and_k()
            veg.calculate_height()

        self.veg = veg
