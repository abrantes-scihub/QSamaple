from qgis.core import (
    Qgis,
    QgsRasterLayer,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessing,
    QgsProject,
    QgsMessageLog
)
from qgis.PyQt.QtCore import QCoreApplication
import os
import numpy as np
from osgeo import gdal, osr
from scipy.spatial import KDTree

# Configure logging
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class NaturalNeighbour(QgsProcessingAlgorithm):
    logger = logging.getLogger(__name__)

    INPUT = 'INPUT'
    FIELD_ANALYSIS = 'FIELD_ANALYSIS'
    OUTPUT_CELL_SIZE = 'OUTPUT_CELL_SIZE'
    OUTPUT = 'OUTPUT'

    def __init__(self):
        super().__init__()
        self.configure_logging()
        self.logger.info("Initialized Natural Neighbour algorithm")

    def configure_logging(self):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
        logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(self.INPUT, self.tr('Input Vector Layer'), types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(self.FIELD_ANALYSIS, self.tr('Field Analysis'), parentLayerParameterName=self.INPUT, type=QgsProcessingParameterField.Numeric, allowMultiple=False))
        self.addParameter(QgsProcessingParameterNumber(self.OUTPUT_CELL_SIZE, self.tr('Output Cell Size'), type=QgsProcessingParameterNumber.Double, minValue=0.0))
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUTPUT, self.tr('Output Raster')))

    def processAlgorithm(self, parameters, context, feedback):
        try:
            # Extract parameters
            input_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
            field_analysis = self.parameterAsString(parameters, self.FIELD_ANALYSIS, context)
            output_cell_size = self.parameterAsDouble(parameters, self.OUTPUT_CELL_SIZE, context)
            output_raster_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

            # Perform Sibson interpolation
            interpolated_values = self.sibson_interpolation(input_layer, field_analysis, output_cell_size)

            # Write interpolated values to output raster
            self.save_interpolated_raster(interpolated_values, output_raster_path, input_layer.extent(), output_cell_size, input_layer.crs())

            return {self.OUTPUT: output_raster_path}

        except Exception as e:
            feedback.reportError(f"An error occurred during processing: {e}")
            return {self.OUTPUT: ''}

    def sibson_interpolation(self, input_layer, field, output_cell_size):
        # Extract points and values from the input layer
        features = [feature for feature in input_layer.getFeatures()]
        points = np.array([feature.geometry().asPoint() for feature in features])
        values = np.array([feature[field] for feature in features])

        # Construct a KD-Tree for the points
        tree = KDTree(points)

        # Create output grid
        extent = input_layer.extent()
        min_x, min_y, max_x, max_y = extent.toRectF().getCoords()
        x_coords = np.arange(min_x, max_x, output_cell_size)
        y_coords = np.arange(min_y, max_y, output_cell_size)
        xx, yy = np.meshgrid(x_coords, y_coords[::-1])  # Reverse y_coords to match orientation

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