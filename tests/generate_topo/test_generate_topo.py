from unittest.mock import patch

import numpy as np
import xarray as xr
from inicheck.config import UserConfig
from numpy import testing as np_test
from rasterio import Affine

from basin_setup.generate_topo import GenerateTopo
from basin_setup.generate_topo.shapefile import Shapefile
from basin_setup.generate_topo.vegetation import Landfire140
from basin_setup.utils import domain_extent, gdal
from tests.Lakes.lakes_test_case import BasinSetupLakes


# patch the landfire datasets for testing. Comment out to test with the
# real thing


@patch.object(Landfire140, 'veg_height_csv',
              new='tests/Lakes/data/landfire_1.4.0/LF_140EVH_05092014.csv')
@patch.object(Landfire140, 'clipped_images', new={
    'veg_type': 'tests/Lakes/data/landfire_1.4.0/clipped_veg_type.tif',
    'veg_height': 'tests/Lakes/data/landfire_1.4.0/clipped_veg_height.tif'
})
class TestBasinSetup(BasinSetupLakes):

    # TODO change extents to this instead of decimals
    # EXTENTS = [318520.0, 4157550.0, 329470.0, 4167900.0]
    EXTENTS = [319570.405027, 4157787.07547, 328270.405027, 4167087.07547]
    EXTENTS_RASTER = [319570.405, 4157787.075, 328270.405, 4167087.075]

    @classmethod
    def setUpClass(self):
        super().setUpClass()

    def setUp(self) -> None:
        self.subject = GenerateTopo(config_file=self.config_file)

    def test_init(self):
        self.assertIsInstance(self.subject.ucfg, UserConfig)

    def test_set_extents(self):
        self.subject.set_extents()
        self.assertListEqual(
            self.subject.extents,
            self.EXTENTS
        )
        self.assertIsInstance(self.subject.transform, Affine)
        self.assertTrue(len(self.subject.x) == 58)
        self.assertTrue(len(self.subject.y) == 62)

    def test_load_basin_shapefiles(self):
        self.subject.load_basin_shapefiles()

        self.assertTrue(len(self.subject.basin_shapefiles) == 1)
        self.assertIsInstance(self.subject.basin_shapefiles[0], Shapefile)
        self.assertEqual(
            self.subject.basin_shapefiles[0].crs.lower(),
            self.CRS.lower()
        )

    def test_load_dem(self):
        self.subject.crs = self.CRS
        self.subject.extents = self.EXTENTS
        self.subject.load_dem()

        extents, cell_size = domain_extent.parse_from_file(
            'tests/Lakes/output/temp/clipped_dem.tif')

        self.assertListEqual(extents, self.EXTENTS_RASTER)
        self.assertTrue(cell_size == self.subject.config['cell_size'])
        self.assertIsInstance(self.subject.dem, xr.DataArray)
        self.assertCountEqual(list(self.subject.dem.coords.keys()), [
                              'y', 'x', 'spatial_ref'])

    @patch.object(Landfire140, 'reproject', return_value=True)
    def test_load_vegetation(self, mock_veg):
        self.subject.crs = self.CRS
        self.subject.extents = self.EXTENTS
        self.subject.load_vegetation()

        extents, cell_size = domain_extent.parse_from_file(
            self.subject.veg.clipped_images['veg_type']
        )

        self.assertListEqual(extents, self.EXTENTS_RASTER)
        self.assertTrue(cell_size == self.subject.config['cell_size'])

        # TODO: Improve decimal precision
        np_test.assert_almost_equal(
            mock_veg.mock_calls[0][1][0],
            self.EXTENTS_RASTER,
            decimal=3,
        )
        self.assertEqual(mock_veg.mock_calls[0][1][1], self.CRS)

        self.assertIsInstance(self.subject.veg, Landfire140)
        self.assertCountEqual(
            list(self.subject.veg.veg_tau_k.coords.keys()),
            ['y', 'x', 'spatial_ref']
        )
        self.assertCountEqual(
            list(self.subject.veg.veg_height.coords.keys()),
            ['y', 'x', 'spatial_ref']
        )
        # Keep heights as float data type for sub-meter classification
        self.assertTrue(
            self.subject.veg.veg_height.dtype == np.float64
        )

    @patch.object(gdal, 'gdalwarp', return_value=True)
    def test_vegetation_resample_method_default(self, mock_veg):
        self.subject.crs = self.CRS
        self.subject.extents = self.EXTENTS
        self.subject.load_vegetation()

        self.assertEqual(mock_veg.call_count, 2)
        # Call 1 to vegetation type
        self.assertEqual(
            mock_veg.mock_calls[0][2]['resample'],
            'mode'
        )
        # Call 2 to vegetation height
        self.assertEqual(
            mock_veg.mock_calls[1][2]['resample'],
            'mode'
        )

    @patch.object(gdal, 'gdalwarp', return_value=True)
    def test_vegetation_resample_method_custom(self, mock_veg):
        nearest_neighbor_resample = 'near'
        self.subject.config['vegetation_type_resample_method'] = \
            nearest_neighbor_resample
        self.subject.config['vegetation_height_resample_method'] = \
            nearest_neighbor_resample
        self.subject.crs = self.CRS
        self.subject.extents = self.EXTENTS
        self.subject.load_vegetation()

        self.assertEqual(mock_veg.call_count, 2)
        # Call 1 to vegetation type
        self.assertEqual(
            mock_veg.mock_calls[0][2]['resample'],
            nearest_neighbor_resample
        )
        # Call 2 to vegetation height
        self.assertEqual(
            mock_veg.mock_calls[1][2]['resample'],
            nearest_neighbor_resample
        )

    @patch.object(Landfire140, 'reproject', return_value=True)
    def test_run(self, _mock_veg):
        gt = GenerateTopo(config_file=self.config_file)
        gt.run()

        ds = xr.open_dataset(self.output_topo, cache=False)

        self.assertCountEqual(
            list(ds.coords.keys()),
            ['y', 'x']
        )
        self.assertCountEqual(
            list(ds.keys()),
            ['dem', 'mask', 'veg_height', 'veg_k',
                'veg_tau', 'veg_type', 'projection']
        )
        ds.close()

        self.compare_netcdf_files('landfire_140/topo.nc', 'topo.nc')
