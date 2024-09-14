from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterDestination,
    QgsProject,
    QgsMessageLog,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsGeometry,
    QgsPointXY,
    Qgis
)
from qgis.PyQt.QtCore import QCoreApplication
import os
import geopandas as gpd
import numpy as np
from osgeo import gdal, osr
from sklearn.neighbors import BallTree
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox
from scipy.spatial import ConvexHull
import matplotlib.path as mplPath

import logging

class NaturalNeighbour(QgsProcessingAlgorithm):
    logger = logging.getLogger(__name__)

    INPUT = 'INPUT'
    FIELD_ANALYSIS = 'FIELD_ANALYSIS'
    OUTPUT_CELL_SIZE = 'OUTPUT_CELL_SIZE'
    MASK_LAYER = 'MASK_LAYER'
    OUTPUT = 'OUTPUT'

    def __init__(self):
        super().__init__()
        self.configure_logging()
        self.logger.info("Initialized Natural Neighbour algorithm")

    def configure_logging(self):
        """
        Configures logging for the algorithm.
        """
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    def initAlgorithm(self, config=None):
        """
        Initializes the algorithm with parameters.
        """
        self.addParameter(QgsProcessingParameterVectorLayer(self.INPUT, self.tr('Input Vector Layer'), types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(self.FIELD_ANALYSIS, self.tr('Field Analysis'), parentLayerParameterName=self.INPUT, type=QgsProcessingParameterField.Numeric, allowMultiple=False))
        self.addParameter(QgsProcessingParameterNumber(self.OUTPUT_CELL_SIZE, self.tr('Output Cell Size'), type=QgsProcessingParameterNumber.Double, minValue=0.0, defaultValue=3.0))
        self.addParameter(QgsProcessingParameterFeatureSource(self.MASK_LAYER, self.tr('Mask Layer'), types=[QgsProcessing.TypeVectorPolygon], optional=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUTPUT, self.tr('Interpolated Map')))

    def processAlgorithm(self, parameters, context, feedback):
        """
        Executes the algorithm to perform Natural Neighbour interpolation.
        """
        try:
            # Extract parameters
            input_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
            field_analysis = self.parameterAsString(parameters, self.FIELD_ANALYSIS, context)
            output_cell_size = self.parameterAsDouble(parameters, self.OUTPUT_CELL_SIZE, context)
            mask_layer = self.parameterAsVectorLayer(parameters, self.MASK_LAYER, context)
            output_raster_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

            # Extract data from the input layer
            data = self.prepareData(input_layer, field_analysis)

            # Perform Sibson interpolation
            if mask_layer:
                data = self.maskData(data, mask_layer, field_analysis, context)

            interpolated_values = self.efficientDiscreteSibsonInterpolation(data, field_analysis, output_cell_size)

            # Write interpolated values to Interpolated Map
            self.saveInterpolatedRaster(interpolated_values, output_raster_path, data.total_bounds, output_cell_size, input_layer.crs(), mask_layer)

            return {self.OUTPUT: output_raster_path}

        except Exception as e:
            feedback.reportError(f"An error occurred during processing: {e}")
            return {self.OUTPUT: ''}

    def prepareData(self, input_layer, field_analysis):
        """
        Converts a QGIS vector layer into a GeoDataFrame.

        Args:
            input_layer (QgsVectorLayer or str): Input vector layer or path to it.
            field_analysis (str): Field name for analysis.

        Returns:
            gpd.GeoDataFrame: GeoDataFrame containing attribute data and geometry.
        """
        try:
            if isinstance(input_layer, QgsVectorLayer):
                layer = input_layer
            else:
                layer = QgsVectorLayer(input_layer, 'temp_layer', 'ogr')

            if not layer.isValid():
                raise Exception('Failed to create QgsVectorLayer from input')

            data = self.qgisVectorLayerToGeoDataFrame(layer)
            
            return data
        
        except Exception as e:
            QgsMessageLog.logMessage(f"Error preparing data: {str(e)}", 'Natural Neighbour', Qgis.Critical)
            return None
            
    def qgisVectorLayerToGeoDataFrame(self, input_layer):
        """
        Converts a QGIS vector layer into a GeoDataFrame.

        Args:
            input_layer (QgsVectorLayer): Input vector layer.

        Returns:
            gpd.GeoDataFrame: GeoDataFrame containing attribute data and geometry.
        """
        try:
            # Retrieve field names from the input layer
            fields = input_layer.fields()
            field_names = [field.name() for field in fields]

            # Extract attribute values for each feature in the input layer
            data = {field_name: [feature[field_name] for feature in input_layer.getFeatures()] for field_name in field_names}

            # Convert the geometry of each feature to WKT format
            geometry = [feature.geometry().asWkt() for feature in input_layer.getFeatures()]

            # Create a dictionary with attribute values and geometry
            data['geometry'] = geometry

            # Create a GeoDataFrame using the dictionary and the WKT geometry,
            # specifying the coordinate reference system (CRS)
            gdf = gpd.GeoDataFrame(data, geometry=gpd.array.from_wkt(geometry), crs=input_layer.crs().toProj4())

            return gdf
        except Exception as e:
            # Log the error message
            QgsMessageLog.logMessage(f"Error in qgisVectorLayerToGeoDataFrame: {str(e)}", 'Natural Neighbour', Qgis.Critical)
            return None

    def maskData(self, data, mask_layer, field, context):
        """
        Masks data based on a polygon mask layer.
        """
        try:
            # Convert mask layer to GeoDataFrame
            mask_data = self.qgisVectorLayerToGeoDataFrame(mask_layer)
            
            if mask_data.empty:
                raise Exception("Mask layer data is empty")

            # Perform spatial join to filter data based on mask layer
            masked_data = gpd.overlay(data, mask_data, how='intersection')

            if masked_data.empty:
                raise Exception("No intersection between data and mask layer")

            # Keep only the original columns from the input data
            masked_data = masked_data[[field] + ['geometry']]
            QgsMessageLog.logMessage(f"Masked data: {masked_data}", 'Natural Neighbour', Qgis.Info)

            return masked_data

        except Exception as e:
            QgsMessageLog.logMessage(f"Error masking data: {str(e)}", 'Natural Neighbour', Qgis.Critical)
            return None

    def efficientDiscreteSibsonInterpolation(self, data, field, output_cell_size):
        """
        Performs efficient discrete Sibson interpolation using only the convex hull approach.
        """
        points = np.column_stack((data.geometry.x, data.geometry.y))
        values = np.array(data[field])

        # Define the extent of the interpolation grid based on the data
        extent = data.total_bounds
        min_x, min_y, max_x, max_y = extent

        # Create grid for interpolation
        x_coords = np.arange(min_x, max_x, output_cell_size)
        y_coords = np.arange(min_y, max_y, output_cell_size)
        xx, yy = np.meshgrid(x_coords, y_coords[::-1])

        # Calculate convex hull of the points
        hull = ConvexHull(points)
        hull_path = mplPath.Path(points[hull.vertices])

        # Create a mask for grid points outside the convex hull
        grid_points = np.c_[xx.ravel(), yy.ravel()]
        hull_mask = hull_path.contains_points(grid_points).reshape(xx.shape)

        # Initialize the interpolation arrays
        c = np.zeros_like(xx.ravel())
        n = np.zeros_like(xx.ravel())

        # Perform the interpolation
        tree = BallTree(points)
        distances, indices = tree.query(grid_points, k=1)
        distances = distances.ravel()
        indices = indices.ravel()
        nearest_values = values[indices]

        for i in range(len(xx.ravel())):
            if not hull_mask.ravel()[i]:
                continue  # Skip points outside the convex hull

            r = distances[i]
            within_radius = (xx - xx.ravel()[i])**2 + (yy - yy.ravel()[i])**2 <= r**2
            indices_within_radius = np.where(within_radius.ravel())[0]

            c[indices_within_radius] += nearest_values[i]
            n[indices_within_radius] += 1

        # Calculate interpolated values
        with np.errstate(divide='ignore', invalid='ignore'):
            interpolated_values = np.divide(c, n, out=np.zeros_like(c), where=n != 0)

        interpolated_values = interpolated_values.reshape(xx.shape)
        interpolated_values[~hull_mask] = np.nan  # Set values outside the convex hull to NaN

        # Log debugging information
        QgsMessageLog.logMessage(f"Extent: {extent}", 'Natural Neighbour', Qgis.Info)
        QgsMessageLog.logMessage(f"Convex hull mask sum: {np.sum(hull_mask)}", 'Natural Neighbour', Qgis.Info)
        QgsMessageLog.logMessage(f"Interpolated values stats: min={np.nanmin(interpolated_values)}, max={np.nanmax(interpolated_values)}", 'Natural Neighbour', Qgis.Info)

        return interpolated_values

    def saveInterpolatedRaster(self, interpolated_values, output_raster_path, extent, output_cell_size, crs, mask_layer=None):
        """
        Saves interpolated values to a GeoTIFF raster.
        """
        rows, cols = interpolated_values.shape

        try:
            # Create Interpolated Map file
            driver = gdal.GetDriverByName('GTiff')
            output_raster = driver.Create(output_raster_path, cols, rows, 1, gdal.GDT_Float32)
            
            if output_raster is None:
                raise IOError(f"Failed to create raster file at {output_raster_path}")

            # Set geo-transform
            output_raster.SetGeoTransform((extent[0] - output_cell_size / 2, output_cell_size, 0, extent[3] + output_cell_size / 2, 0, -output_cell_size))

            # Set NoData value
            output_band = output_raster.GetRasterBand(1)
            output_band.SetNoDataValue(np.nan)
            
            # Write interpolated values to Interpolated Map
            output_band.WriteArray(interpolated_values)
            
            # Set spatial reference
            srs = osr.SpatialReference()
            srs.ImportFromWkt(crs.toWkt())
            output_raster.SetProjection(srs.ExportToWkt())
            
            # Clip raster using mask layer
            if mask_layer:
                QgsMessageLog.logMessage(f"Clipping raster with mask layer: {mask_layer.source()}", 'Natural Neighbour', Qgis.Info)
                gdal.Warp(output_raster_path, output_raster, cutlineDSName=mask_layer.source(), cropToCutline=True, dstNodata=np.nan)
            
            # Close the raster file
            output_band.FlushCache()
            output_raster = None
            
            # Load the saved raster layer into QGIS
            output_raster_layer = QgsRasterLayer(output_raster_path, 'Interpolated Raster')
            QgsProject.instance().addMapLayer(output_raster_layer)

        except Exception as e:
            QgsMessageLog.logMessage(f"Error saving interpolated raster: {str(e)}", 'Natural Neighbour', Qgis.Critical)
            if output_raster:
                output_raster = None  # Ensure raster is properly closed on error

            return None
        
        return output_raster_path

    def name(self):
        return 'Natural Neighbour'

    def displayName(self):
        return self.tr(self.name())

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return 'Interpolation'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def shortHelpString(self):
        return ("Natural Neighbour Interpolation. \n"
                "Performs Natural Neighbour interpolation. \n"
                "Specify the field for analysis and the output cell size. \n")

    def createInstance(self):
        return NaturalNeighbour()

    def icon(self):
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath, 'styles', 'icon.png'))