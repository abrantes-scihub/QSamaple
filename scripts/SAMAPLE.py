from qgis.core import QgsProcessing
from qgis.core import QgsProcessingAlgorithm
from qgis.core import QgsProcessingMultiStepFeedback
from qgis.core import QgsProcessingParameterRasterLayer
from qgis.core import QgsProcessingParameterFeatureSource
from qgis.core import QgsProcessingParameterNumber
from qgis.core import QgsProcessingParameterField
from qgis.core import QgsProcessingParameterFeatureSink
from qgis.core import QgsProcessingParameterRasterDestination
import processing
import os
from qgis.PyQt.QtGui import QIcon


class SAMAPLE(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer('input_raster_layer', 'Input Raster Layer', defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSource('mask_layer', 'Mask Layer', types=[QgsProcessing.TypeVectorAnyGeometry], defaultValue=None))
        self.addParameter(QgsProcessingParameterNumber('output_cell_size', 'Output Cell Size', type=QgsProcessingParameterNumber.Double, defaultValue=None))
        self.addParameter(QgsProcessingParameterField('measuredreference_data_field', 'Measured/Reference data field', type=QgsProcessingParameterField.Numeric, parentLayerParameterName='mask_layer', allowMultiple=False, defaultValue=None))
        self.addParameter(QgsProcessingParameterNumber('number_of_clusters', 'Number of Clusters', optional=True, type=QgsProcessingParameterNumber.Integer, minValue=1, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('LocalMoransIWithoutOutliers', "Local Moran's I without Outliers", type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Outliers', 'Outliers', optional=True, type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=False, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('LocalMoransI', "Local Moran's I", type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=True, defaultValue='TEMPORARY_OUTPUT'))
        self.addParameter(QgsProcessingParameterFeatureSink('ClusteredLayer', 'Clustered Layer', type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterDestination('InterpolatedMap', 'Interpolated Map', createByDefault=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Accuracy', 'Accuracy', type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=True, defaultValue=None))

    def processAlgorithm(self, parameters, context, model_feedback):
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
        feedback = QgsProcessingMultiStepFeedback(7, model_feedback)
        results = {}
        outputs = {}

        # Clip raster by mask layer
        alg_params = {
            'ALPHA_BAND': False,
            'CROP_TO_CUTLINE': True,
            'DATA_TYPE': 0,  # Use Input Layer Data Type
            'EXTRA': '',
            'INPUT': parameters['input_raster_layer'],
            'KEEP_RESOLUTION': False,
            'MASK': parameters['mask_layer'],
            'MULTITHREADING': False,
            'NODATA': -9999,
            'OPTIONS': '',
            'SET_RESOLUTION': False,
            'SOURCE_CRS': None,
            'TARGET_CRS': None,
            'TARGET_EXTENT': None,
            'X_RESOLUTION': None,
            'Y_RESOLUTION': None,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ClipRasterByMaskLayer'] = processing.run('gdal:cliprasterbymasklayer', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # Raster pixels to points
        alg_params = {
            'FIELD_NAME': 'VALUE',
            'INPUT_RASTER': outputs['ClipRasterByMaskLayer']['OUTPUT'],
            'RASTER_BAND': 1,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RasterPixelsToPoints'] = processing.run('native:pixelstopoints', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # Local Moran's I
        alg_params = {
            'INPUT': outputs['RasterPixelsToPoints']['OUTPUT'],
            'KNN_DIST': 8,
            'MASK_LAYER': parameters['mask_layer'],
            'METHOD': 2,  # K Nearest Neighbors
            'VARIABLE': 'VALUE',
            'OUTPUT': parameters['LocalMoransI']
        }
        outputs['LocalMoransI'] = processing.run('samaple_provider:Local Moran\'s I', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['LocalMoransI'] = outputs['LocalMoransI']['OUTPUT']

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # Extract by expression
        outliers_param = parameters.get('Outliers')
        alg_params = {
            'EXPRESSION': '"LMIType" = \'HH\' OR "LMIType" = \'LL\' OR "LMIType" = \'NS\'',
            'INPUT': outputs['LocalMoransI']['OUTPUT'],
            'FAIL_OUTPUT': QgsProcessing.TEMPORARY_OUTPUT if outliers_param is None else outliers_param,
            'OUTPUT': parameters['LocalMoransIWithoutOutliers']
        }
        outputs['ExtractByExpression'] = processing.run('native:extractbyexpression', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['LocalMoransIWithoutOutliers'] = outputs['ExtractByExpression']['OUTPUT']
        
        if outliers_param:
            results['Outliers'] = outputs['ExtractByExpression']['FAIL_OUTPUT']

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # Multivariate Clustering
        alg_params = {
            'ANALYSIS_FIELDS': ['VALUE'],
            'CLUSTERING_METHOD': 0,  # K means
            'INITIALIZATION_METHOD': 0,  # Optimized seed locations
            'INPUT': outputs['ExtractByExpression']['OUTPUT'],
            'MASK_LAYER': parameters['mask_layer'],
            'NUM_CLUSTERS': parameters['number_of_clusters'],
            'OUTPUT': parameters['ClusteredLayer'],
            'OUTPUT_EVALUATION_TABLE': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['MultivariateClustering'] = processing.run('samaple_provider:Multivariate Clustering', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['ClusteredLayer'] = outputs['MultivariateClustering']['OUTPUT']

        feedback.setCurrentStep(5)
        if feedback.isCanceled():
            return {}

        # Natural Neighbour
        alg_params = {
            'FIELD_ANALYSIS': 'VALUE',
            'INPUT': outputs['MultivariateClustering']['OUTPUT'],
            'MASK_LAYER': parameters['mask_layer'],
            'OUTPUT_CELL_SIZE': parameters['output_cell_size'],
            'OUTPUT': parameters['InterpolatedMap']
        }
        outputs['NaturalNeighbour'] = processing.run('samaple_provider:Natural Neighbour', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['InterpolatedMap'] = outputs['NaturalNeighbour']['OUTPUT']

        feedback.setCurrentStep(6)
        if feedback.isCanceled():
            return {}

        # Accuracy Metrics
        alg_params = {
            'CASE_FIELD': 'Cluster',
            'ESTIMATED_DATA': 'VALUE',
            'INPUT': outputs['MultivariateClustering']['OUTPUT'],
            'MEASURED_DATA': parameters['measuredreference_data_field'],
            'OUTPUT': parameters['Accuracy']
        }
        outputs['AccuracyMetrics'] = processing.run('samaple_provider:accuracymetrics', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Accuracy'] = outputs['AccuracyMetrics']['OUTPUT']
        
        return results


    def name(self):
        return 'SAMAPLE'

    def displayName(self):
        return 'SAMAPLE'

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return (
            "SAMAPLE (Semi-Automatic Mapping and Accuracy Analysis in Plot-level Experiment) is a QGIS plugin for analyzing raster and vector data.\n\n"
            "This tool performs the following steps:\n"
            "1. Clips a raster layer using a mask layer.\n"
            "2. Converts raster pixels to point features.\n"
            "3. Calculates Local Moran's I statistic to identify spatial clusters.\n"
            "4. Extracts features based on Local Moran's I results, filtering outliers if specified.\n"
            "5. Performs multivariate clustering on the filtered data.\n"
            "6. Interpolates the clustered data using Natural Neighbor interpolation.\n"
            "7. Calculates accuracy metrics comparing estimated data with measured/reference data.\n\n"
            "Parameters:\n"
            "- Input Raster Layer: The raster layer to be processed.\n"
            "- Mask Layer: Vector layer used to mask the raster layer.\n"
            "- Output Cell Size: The cell size for the output raster in the interpolation step.\n"
            "- Measured/Reference Data Field: The field in the mask layer containing reference values for accuracy assessment.\n"
            "- Number of Clusters: The number of clusters for the multivariate clustering step (optional).\n"
            "- Local Moran's I without Outliers: Output vector layer of Local Moran's I statistic without outliers.\n"
            "- Outliers: Output vector layer of features identified as outliers (optional).\n"
            "- Local Moran's I: Output vector layer of Local Moran's I statistic including all features.\n"
            "- Clustered Layer: Output vector layer with multivariate clustering results.\n"
            "- Interpolated Map: Output raster layer of interpolated data.\n"
            "- Accuracy: Output vector layer with accuracy metrics.\n"
            "Note: The 'Outliers' output will be skipped by default if not specified."
        )

    def createInstance(self):
        return SAMAPLE()

    def icon(self):
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath, 'styles', 'icon.png'))