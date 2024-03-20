from qgis.core import (
    Qgis,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsSingleBandGrayRenderer,
    QgsRasterFileWriter,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessing,
    QgsProject,
    QgsWkbTypes,
    QgsMessageLog
)
from qgis.PyQt.QtCore import QCoreApplication
import os
import numpy as np
from scipy.interpolate import griddata
from osgeo import gdal, osr
from scipy.interpolate import CloughTocher2DInterpolator

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

        # Initialize QgsMessageLog
        QgsMessageLog.logMessage("Logging initialized", 'NaturalNeighbour', level=Qgis.Info)

    def processAlgorithm(self, parameters, context, feedback):
        try:
            # Extract parameters
            input_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
            field_analysis = self.parameterAsString(parameters, self.FIELD_ANALYSIS, context)
            output_cell_size = self.parameterAsDouble(parameters, self.OUTPUT_CELL_SIZE, context)
            output_raster_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

            # Log input parameters
            QgsMessageLog.logMessage(f"Input layer: {input_layer.name()}", 'NaturalNeighbour', level=Qgis.Info)
            QgsMessageLog.logMessage(f"Field analysis: {field_analysis}", 'NaturalNeighbour', level=Qgis.Info)
            QgsMessageLog.logMessage(f"Output cell size: {output_cell_size}", 'NaturalNeighbour', level=Qgis.Info)

            # Perform natural neighbor interpolation
            interpolated_values = self.natural_neighbor_interpolation(input_layer, field_analysis, output_cell_size)

            # Log interpolated values
            self.logger.debug(f"Interpolated values: {interpolated_values}")

            # Write interpolated values to output raster
            self.save_interpolated_raster(interpolated_values, output_raster_path, input_layer.extent(), output_cell_size, input_layer.crs())

            return {self.OUTPUT: output_raster_path}

        except Exception as e:
            feedback.reportError(f"An error occurred during processing: {e}")
            return {self.OUTPUT: ''}







    def natural_neighbor_interpolation(self, input_layer, field, output_cell_size):
        # Log information
        QgsMessageLog.logMessage("Performing natural neighbor interpolation", 'NaturalNeighbour', level=Qgis.Info)

        # Extract points and values from the input layer
        points = []
        values = []
        for feature in input_layer.getFeatures():
            point = feature.geometry().asPoint()
            points.append(point)
            values.append(feature[field])

        points = np.array(points)
        values = np.array(values)

        # Define the grid based on the desired extent and the output cell size
        extent = input_layer.extent()
        min_x, min_y, max_x, max_y = extent.toRectF().getCoords()
        # Adjust the extent to cover a slightly larger area
        min_x -= output_cell_size
        max_y += output_cell_size  # Reverse the order of y-coordinates to avoid mirroring
        x_coords = np.arange(min_x, max_x, output_cell_size)
        y_coords = np.arange(max_y, min_y, -output_cell_size)  # Reverse the order of y-coordinates
        xx, yy = np.meshgrid(x_coords, y_coords)

        # Log grid information
        QgsMessageLog.logMessage(f"Grid size: {xx.shape}", 'NaturalNeighbour', level=Qgis.Info)

        # Perform natural neighbor interpolation using CloughTocher2DInterpolator
        interpolator = CloughTocher2DInterpolator(points, values)
        interpolated_values = interpolator(xx, yy)

        # Log completion
        QgsMessageLog.logMessage("Interpolation completed", 'NaturalNeighbour', level=Qgis.Info)

        return interpolated_values







    def save_interpolated_raster(self, interpolated_values, output_raster_path, extent, output_cell_size, crs):
        # Log information
        QgsMessageLog.logMessage("Saving interpolated raster", 'NaturalNeighbour', level=Qgis.Info)

        rows, cols = interpolated_values.shape

        # Log interpolated raster dimensions and content
        QgsMessageLog.logMessage(f"Interpolated raster dimensions: {rows} rows x {cols} columns", 'NaturalNeighbour', level=Qgis.Info)
        QgsMessageLog.logMessage(f"Interpolated raster content: {interpolated_values}", 'NaturalNeighbour', level=Qgis.Info)

        # Create output raster file
        driver = gdal.GetDriverByName('GTiff')
        output_raster = driver.Create(output_raster_path, cols, rows, 1, gdal.GDT_Float32)
        output_raster.SetGeoTransform((extent.xMinimum(), output_cell_size, 0, extent.yMaximum(), 0, -output_cell_size))
        output_band = output_raster.GetRasterBand(1)
        output_band.SetNoDataValue(np.nan)

        # Write interpolated values to output raster
        output_band.WriteArray(interpolated_values)

        # Check if the output raster is empty
        raster_statistics = output_band.GetStatistics(0, 1)
        self.logger.debug(f"Raster statistics: {raster_statistics}")

        if all(stat == 0 for stat in raster_statistics):
            self.logger.warning("Output raster is empty")

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

        # Log completion
        QgsMessageLog.logMessage("Raster saved successfully", 'NaturalNeighbour', level=Qgis.Info)


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
