from qgis.core import (QgsProcessing,
                       QgsProcessingAlgorithm,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterField,
                       QgsProcessingParameterNumber,
                       QgsFeatureSink,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterEnum,
                       QgsVectorLayer,
                       QgsProcessingUtils,
                       QgsMessageLog,
                       Qgis
                       )
from PyQt5.QtCore import QCoreApplication
import os, tempfile, random, string
import geopandas as gpd
import pandas as pd
import libpysal
from esda.moran import Moran_Local
import logging

class LocalMoransI(QgsProcessingAlgorithm):
    """
    All Processing algorithms should extend the QgsProcessingAlgorithm
    class.

    """
    # Constants used to refer to parameters and outputs. They will be
    # used when calling the algorithm from another algorithm, or when
    # calling from the QGIS console.
    
    INPUT = 'INPUT'
    MASK_LAYER = 'MASK_LAYER'
    VARIABLE = 'VARIABLE'
    METHOD = 'METHOD'
    KNN_DIST = 'KNN_DIST'
    OUTPUT = 'OUTPUT'


    def __init__(self):
        self.dest_id = None
        super().__init__()

    def initAlgorithm(self, config):
        """
        Here we define the inputs and output of the algorithm, along
        with some other properties.
        """
        self.addParameter(QgsProcessingParameterVectorLayer(self.INPUT, 'Input layer', types=[QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorPoint], defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSource(self.MASK_LAYER, 'Mask layer', types=[QgsProcessing.TypeVectorPolygon], optional=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterField(self.VARIABLE, 'Variable X', type=QgsProcessingParameterField.Numeric, parentLayerParameterName=self.INPUT))
        self.addParameter(QgsProcessingParameterEnum(self.METHOD, 'Method', options = ['Queen contiguity', 'Rook contiguity', 'K Nearest Neighbors', 'Distance Band'], defaultValue=2))
        self.addParameter(QgsProcessingParameterNumber(self.KNN_DIST, type = QgsProcessingParameterNumber.Integer,description='K Neighbors / Distance threshold (only for KNN / Distance Band methods)', defaultValue = 8, minValue = 1))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, 'Local Morans I', createByDefault=True, supportsAppend=False, defaultValue=None))

    def processAlgorithm(self, parameters, context, feedback):
        layer_source = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        mask_layer = self.parameterAsVectorLayer(parameters, self.MASK_LAYER, context)  # New parameter retrieval
        field = self.parameterAsString(parameters, self.VARIABLE, context)
        method = self.parameterAsInt(parameters, self.METHOD, context)
        knn_dist = self.parameterAsDouble(parameters, self.KNN_DIST, context)

        data, temp_path, polygon_column, randExt = self.prepareData(layer_source, field, method, knn_dist, context)

        if mask_layer:  # Check if mask layer is provided
            data = self.maskData(data, mask_layer, field, context)

        w = self.createSpatialWeights(data, method, knn_dist)

        # Calculate Moran's I
        y = data[field]
        local_moran = self.calculateMoransI(y, w)

        # If Local morans was calculated with centroids, change dataframe back to polygons
        if layer_source.geometryType() == 2 and (method == 2 or method == 3): 
            data['geometry'] = polygon_column

        # Join results
        data = self.joinResults(data, local_moran)

        # Output
        self.handleOutput(parameters, context, data, temp_path, layer_source, randExt)
        return {self.OUTPUT: self.dest_id}

    def maskData(self, data, mask_layer, field, context):
        try:
            # Convert mask layer to GeoDataFrame
            mask_data = self.qgis_vector_layer_to_geodataframe(mask_layer)

            # Perform spatial join to filter data based on mask layer
            masked_data = gpd.overlay(data, mask_data, how='intersection')

            # Keep only the original columns from the input data
            masked_data = masked_data[[field] + ['geometry']]

            return masked_data
        except Exception as e:
            QgsMessageLog.logMessage(f"Error masking data: {str(e)}", 'Local Morans I', Qgis.Critical)
            return None


    def prepareData(self, layer_source, field, method, knn_dist, context):
        if isinstance(layer_source, QgsVectorLayer):
            # Handle QgsVectorLayer object
            layer = layer_source
        else:
            # Handle layer source string (shapefile path or temporary layer source)
            layer = QgsVectorLayer(layer_source, 'temporary_layer', 'ogr')

        if not layer.isValid():
            raise Exception('Failed to create QgsVectorLayer from input')

        data = self.qgis_vector_layer_to_geodataframe(layer)

        polygon_column = None  # Initialize polygon_column variable

        if layer.geometryType() == 2 and (method == 2 or method == 3):
            polygon_column = data['geometry']
            data['geometry'] = data.centroid

        randExt = ''.join(random.choices(string.ascii_letters + string.digits, k=8))  # Generate random extension

        temp_path = os.path.join(tempfile.gettempdir(), f'temp_lmi_{randExt}.shp')
        data.to_file(temp_path)

        return data, temp_path, polygon_column, randExt  # Return randExt

    def qgis_vector_layer_to_geodataframe(self, layer_source):
        fields = layer_source.fields()
        field_names = [field.name() for field in fields]

        data = {field_name: [feature[field_name] for feature in layer_source.getFeatures()] for field_name in field_names}

        geometry = [feature.geometry().asWkt() for feature in layer_source.getFeatures()]
        data['geometry'] = geometry

        gdf = gpd.GeoDataFrame(data, geometry=gpd.array.from_wkt(geometry), crs=layer_source.crs().toProj4())
        return gdf


    def createSpatialWeights(self, data, method, knn_dist):
        try:
            if method == 0:
                # Create spatial weights based on centroids
                w = libpysal.weights.contiguity.Queen.from_dataframe(data, ids=data.index.tolist())
            elif method == 1:
                # Create spatial weights based on centroids
                w = libpysal.weights.contiguity.Rook.from_dataframe(data, ids=data.index.tolist())
            elif method == 2:
                # Create spatial weights based on centroids
                w = libpysal.weights.distance.KNN.from_dataframe(data, k=int(knn_dist), ids=data.index.tolist())
            elif method == 3:
                # Create spatial weights based on centroids
                w = libpysal.weights.distance.DistanceBand.from_dataframe(data, threshold=knn_dist, ids=data.index.tolist())

            return w
        except Exception as e:
            QgsMessageLog.logMessage(f"Error creating spatial weights: {str(e)}", 'Local Morans I', Qgis.Critical)
            return None

    def calculateMoransI(self, y, w):
        local_moran = Moran_Local(y, w)
        return local_moran

    def joinResults(self, data, local_moran):
        lmi = local_moran.Is
        lmq = local_moran.q
        lmp = local_moran.p_z_sim

        data = data.join(pd.DataFrame(lmi, columns=['LMI']))
        data = data.join(pd.DataFrame(lmp, columns=['LMP']))
        data = data.join(pd.DataFrame(lmq, columns=['LMQ']))

        return data

    def handleOutput(self, parameters, context, data, temp_path, layer_source, randExt):
        out_path = os.path.join(tempfile.gettempdir(), f'temp_lmi_{randExt}.shp')
        data.to_file(out_path)
        vector_layer = QgsVectorLayer(out_path, "Local Morans I", "ogr")
        vector_layer.setCrs(layer_source.crs())

        source = vector_layer
        (sink, dest_id) = self.parameterAsSink(parameters, self.OUTPUT, context, source.fields(), source.wkbType(), source.sourceCrs())
        features = source.getFeatures()
        for current, feature in enumerate(features):
            sink.addFeature(feature, QgsFeatureSink.FastInsert)

        self.dest_id = dest_id

    def postProcessAlgorithm(self, context, feedback):
        os.chdir(os.path.dirname(__file__))
        currentPath = os.getcwd()
        processed_layer = QgsProcessingUtils.mapLayerFromString(self.dest_id, context)

        if processed_layer.geometryType() == 0:
            processed_layer.loadNamedStyle(currentPath + '/styles/LocalMoransPoints.qml')
        elif processed_layer.geometryType() == 2:
            processed_layer.loadNamedStyle(currentPath + '/styles/LocalMoransPolygons.qml')

        return {self.OUTPUT: self.dest_id}
        
    def name(self):
        """
        Returns the algorithm name, used for identifying the algorithm. This
        string should be fixed for the algorithm, and must not be localised.
        The name should be unique within each provider. Names should contain
        lowercase alphanumeric characters only and no spaces or other
        formatting characters.
        """
        return 'Local Morans I'

    def displayName(self):
        """
        Returns the translated algorithm name, which should be used for any
        user-visible display of the algorithm name.
        """
        return self.tr(self.name())

    def group(self):
        """
        Returns the name of the group this algorithm belongs to. This string
        should be localised.
        """
        return self.tr(self.groupId())

    def groupId(self):
        """
        Returns the unique ID of the group this algorithm belongs to. This
        string should be fixed for the algorithm, and must not be localised.
        The group id should be unique within each provider. Group id should
        contain lowercase alphanumeric characters only and no spaces or other
        formatting characters.
        """
        return 'Spatial Analysis'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)
    
    def shortHelpString(self):
        return ("Local Morans I. \n"
        		"There are three available methods:\n"
        		"- Queen contiguity in which areas with common edges or corners are considered neighbors (works only for polygon layers).\n"
        		"- K Nearest Neighbors (works with point/polygon* layers).\n"
       			"- Distance Band, in which areas or points within a fixed distance are considered neighbors (works with point/polygon* layers).\n"
      			"*In KNN and Distance Band, Morans I for polygon layers is calculated based on their centroids.")

    def createInstance(self):
        return LocalMoransI()
    
    def icon(self):
        from qgis.PyQt.QtGui import QIcon
        import os
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath,'styles','icon.png'))
