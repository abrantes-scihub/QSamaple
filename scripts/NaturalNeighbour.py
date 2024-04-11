from qgis.core import (
                    Qgis,
                    QgsRasterLayer,
                    QgsProcessingAlgorithm,
                    QgsProcessingParameterVectorLayer,
                    QgsProcessingParameterField,
                    QgsProcessingParameterNumber,
                    QgsProcessingParameterFeatureSource,
                    QgsProcessingParameterRasterDestination,
                    QgsProcessing,
                    QgsProject,
                    QgsMessageLog,
                    QgsVectorLayer,
                    QgsPointXY,
                    QgsGeometry,
                    QgsWkbTypes
                    )
from qgis.PyQt.QtCore import QCoreApplication
import os
import geopandas as gpd
import numpy as np
from osgeo import gdal, osr
from scipy.spatial import cKDTree  # Ball Tree

# Configure logging
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(self.INPUT, self.tr('Input Vector Layer'), types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(self.FIELD_ANALYSIS, self.tr('Field Analysis'), parentLayerParameterName=self.INPUT, type=QgsProcessingParameterField.Numeric, allowMultiple=False))
        self.addParameter(QgsProcessingParameterNumber(self.OUTPUT_CELL_SIZE, self.tr('Output Cell Size'), type=QgsProcessingParameterNumber.Double, minValue=0.0, defaultValue=3.0))
        self.addParameter(QgsProcessingParameterFeatureSource(self.MASK_LAYER, self.tr('Mask Layer'), types=[QgsProcessing.TypeVectorPolygon], optional=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUTPUT, self.tr('Output Raster')))

    def processAlgorithm(self, parameters, context, feedback):
        try:
            # Extract parameters
            input_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
            field_analysis = self.parameterAsString(parameters, self.FIELD_ANALYSIS, context)
            output_cell_size = self.parameterAsDouble(parameters, self.OUTPUT_CELL_SIZE, context)
            mask_layer = self.parameterAsVectorLayer(parameters, self.MASK_LAYER, context)
            output_raster_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

            # Perform Sibson interpolation
            if mask_layer:
                interpolated_values = self.efficientDiscreteSibsonInterpolation(input_layer, field_analysis, output_cell_size, mask_layer)
            else:
                interpolated_values = self.efficientDiscreteSibsonInterpolation(input_layer, field_analysis, output_cell_size)

            # Write interpolated values to output raster
            self.save_interpolated_raster(interpolated_values, output_raster_path, input_layer.extent(), output_cell_size, input_layer.crs())

            return {self.OUTPUT: output_raster_path}

        except Exception as e:
            feedback.reportError(f"An error occurred during processing: {e}")
            return {self.OUTPUT: ''}

    def apply_mask(self, data, mask_layer, field, context):
        try:
            # Convert mask layer to GeoDataFrame
            mask_data = self.qgisVectorLayerToGeoDataFrame(mask_layer)

            # Perform spatial join to filter data based on mask layer
            masked_data = gpd.overlay(data, mask_data, how='intersection')

            # Keep only the original columns from the input data
            masked_data = masked_data[[field] + ['geometry']]

            return masked_data

        except Exception as e:
            QgsMessageLog.logMessage(f"Error masking data: {str(e)}", 'Natural Neighbour', Qgis.Critical)
            return None

            
    def qgisVectorLayerToGeoDataFrame(self, input_layer):
        try:
            # Retrieve field names from the input layer
            fields = input_layer.fields()
            field_names = [field.name() for field in fields]

            # Extract attribute values for each feature in the input layer
            data = {field_name: [] for field_name in field_names}
            for feature in input_layer.getFeatures():
                for field_name in field_names:
                    data[field_name].append(feature[field_name])

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


    def efficientDiscreteSibsonInterpolation(self, input_layer, field, output_cell_size, mask_layer=None):
        # Define the data source based on whether mask_layer is provided
        if mask_layer and mask_layer.isValid():
            data_source = mask_layer
        else:
            data_source = input_layer

        # Extract points and values from the data source
        features = [feature for feature in data_source.getFeatures()]
        points = np.array([feature.geometry().asPoint() for feature in features])
        values = np.array([feature[field] for feature in features])

        # Define the extent based on the mask_layer if available, otherwise use the input_layer extent
        if mask_layer and mask_layer.isValid():
            extent = mask_layer.extent()
        else:
            extent = input_layer.extent()

        min_x, min_y, max_x, max_y = extent.toRectF().getCoords()
        x_coords = np.arange(min_x, max_x, output_cell_size)
        y_coords = np.arange(min_y, max_y, output_cell_size)
        xx, yy = np.meshgrid(x_coords, y_coords[::-1])  # Reverse y_coords to match orientation

        # Replace KDTree with Ball Tree
        tree = cKDTree(points)

        # Initialize arrays for accumulating values
        c = np.zeros_like(xx)
        n = np.zeros_like(xx)

        # Iterate over raster positions
        for i in range(xx.shape[0]):
            for j in range(xx.shape[1]):
                # Find the closest site and calculate radius
                _, indices = tree.query([(xx[i][j], yy[i][j])], k=1)
                index = indices[0]
                nearest_point = points[index]
                r = np.linalg.norm([xx[i][j], yy[i][j]] - nearest_point)

                # Iterate over nearby raster positions within the radius
                p_range = np.where((xx - xx[i][j])**2 + (yy - yy[i][j])**2 <= r**2)
                c[p_range] += values[index]
                n[p_range] += 1

        # Compute interpolated values
        with np.errstate(divide='ignore', invalid='ignore'):
            interpolated_values = np.divide(c, n, out=np.zeros_like(c), where=n != 0)

        return interpolated_values


    def save_interpolated_raster(self, interpolated_values, output_raster_path, extent, output_cell_size, crs):
        rows, cols = interpolated_values.shape

        # Create output raster file
        driver = gdal.GetDriverByName('GTiff')
        output_raster = driver.Create(output_raster_path, cols, rows, 1, gdal.GDT_Float32)
        output_raster.SetGeoTransform((extent.xMinimum(), output_cell_size, 0, extent.yMaximum(), 0, -output_cell_size))
        output_band = output_raster.GetRasterBand(1)
        output_band.SetNoDataValue(np.nan)

        # Write interpolated values to output raster
        output_band.WriteArray(interpolated_values)

        # Set spatial reference
        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs.toWkt())
        output_raster.SetProjection(srs.ExportToWkt())

        # Close the raster file
        output_band.FlushCache()
        output_raster = None

        # Load the saved raster layer
        output_raster_layer = QgsRasterLayer(output_raster_path, 'Interpolated Raster')
        QgsProject.instance().addMapLayer(output_raster_layer)

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
        from qgis.PyQt.QtGui import QIcon
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath, 'styles', 'icon.png'))