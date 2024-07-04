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
                       Qgis)
from PyQt5.QtCore import QCoreApplication
import os
import tempfile
import random
import string
import geopandas as gpd
import pandas as pd
import libpysal
from esda.moran import Moran_Local
from qgis.PyQt.QtGui import QIcon

class LocalMoransI(QgsProcessingAlgorithm):
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
        self.addParameter(QgsProcessingParameterVectorLayer(self.INPUT, 'Input layer', types=[QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorPoint], defaultValue=None))
        self.addParameter(QgsProcessingParameterField(self.VARIABLE, 'Variable X', type=QgsProcessingParameterField.Numeric, parentLayerParameterName=self.INPUT))
        self.addParameter(QgsProcessingParameterFeatureSource(self.MASK_LAYER, 'Mask layer', types=[QgsProcessing.TypeVectorPolygon], optional=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterEnum(self.METHOD, 'Method', options=['Queen contiguity', 'Rook contiguity', 'K Nearest Neighbors', 'Distance Band'], defaultValue=2))
        self.addParameter(QgsProcessingParameterNumber(self.KNN_DIST, type=QgsProcessingParameterNumber.Integer, description='K Neighbors / Distance threshold (only for KNN / Distance Band methods)', defaultValue=8, minValue=1))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, 'Local Morans I', createByDefault=True, supportsAppend=False, defaultValue=None))

    def processAlgorithm(self, parameters, context, feedback):
        input_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        field = self.parameterAsString(parameters, self.VARIABLE, context)
        mask_layer = self.parameterAsVectorLayer(parameters, self.MASK_LAYER, context)
        method = self.parameterAsInt(parameters, self.METHOD, context)
        knn_dist = self.parameterAsDouble(parameters, self.KNN_DIST, context)

        data, temp_path, polygon_column, randExt = self.prepareData(input_layer, field, method, knn_dist, context)

        if mask_layer:
            data = self.maskData(data, mask_layer, field, context)

        w = self.createSpatialWeights(data, method, knn_dist)

        y = data[field]
        local_moran = self.calculateMoransI(y, w)

        if input_layer.geometryType() == 2 and (method == 2 or method == 3):
            data['geometry'] = polygon_column

        data = self.joinResults(data, local_moran)

        self.handleOutput(parameters, context, data, temp_path, input_layer, randExt)

        return {self.OUTPUT: self.dest_id}

    def maskData(self, data, mask_layer, field, context):
        try:
            mask_data = self.qgisVectorLayerToGeoDataFrame(mask_layer)
            masked_data = gpd.overlay(data, mask_data, how='intersection')
            masked_data = masked_data[[field] + ['geometry']]
            return masked_data
        except Exception as e:
            QgsMessageLog.logMessage(f"Error masking data: {str(e)}", 'Local Morans I', Qgis.Critical)
            return None

    def prepareData(self, input_layer, field, method, knn_dist, context):
        layer = input_layer if isinstance(input_layer, QgsVectorLayer) else QgsVectorLayer(input_layer, 'temporary_layer', 'ogr')

        if not layer.isValid():
            raise Exception('Failed to create QgsVectorLayer from input')

        data = self.qgisVectorLayerToGeoDataFrame(layer)

        polygon_column = None
        if layer.geometryType() == 2 and (method == 2 or method == 3):
            polygon_column = data['geometry']
            data['geometry'] = data.centroid

        randExt = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        temp_path = os.path.join(tempfile.gettempdir(), f'temp_lmi_{randExt}.shp')
        data.to_file(temp_path)

        return data, temp_path, polygon_column, randExt

    def qgisVectorLayerToGeoDataFrame(self, input_layer):
        fields = input_layer.fields()
        field_names = [field.name() for field in fields]

        data = {field_name: [feature[field_name] for feature in input_layer.getFeatures()] for field_name in field_names}
        geometry = [feature.geometry().asWkt() for feature in input_layer.getFeatures()]
        data['geometry'] = geometry

        gdf = gpd.GeoDataFrame(data, geometry=gpd.array.from_wkt(geometry), crs=input_layer.crs().toProj4())

        return gdf

    def createSpatialWeights(self, data, method, knn_dist):
        try:
            if method == 0:
                w = libpysal.weights.contiguity.Queen.from_dataframe(data, ids=data.index.tolist())
            elif method == 1:
                w = libpysal.weights.contiguity.Rook.from_dataframe(data, ids=data.index.tolist())
            elif method == 2:
                w = libpysal.weights.distance.KNN.from_dataframe(data, k=int(knn_dist), ids=data.index.tolist())
            elif method == 3:
                w = libpysal.weights.distance.DistanceBand.from_dataframe(data, threshold=knn_dist, ids=data.index.tolist())
            return w
        except Exception as e:
            QgsMessageLog.logMessage(f"Error creating spatial weights: {str(e)}", 'Local Morans I', Qgis.Critical)
            return None

    def calculateMoransI(self, y, w):
        return Moran_Local(y, w)

    def joinResults(self, data, local_moran):
        lmi = local_moran.Is
        lmq = local_moran.q
        lmp = local_moran.p_z_sim

        data = data.join(pd.DataFrame(lmi, columns=['LMI']))
        data = data.join(pd.DataFrame(lmp, columns=['LMP']))
        data = data.join(pd.DataFrame(lmq, columns=['LMQ']))

        # Calculate LMIType and add Code field
        significance_level = 0.05
        data['LMIType'] = 'NS'
        data.loc[(data['LMP'] <= significance_level) & (data['LMQ'] == 1), 'LMIType'] = 'HH'
        data.loc[(data['LMP'] <= significance_level) & (data['LMQ'] == 2), 'LMIType'] = 'LH'
        data.loc[(data['LMP'] <= significance_level) & (data['LMQ'] == 3), 'LMIType'] = 'LL'
        data.loc[(data['LMP'] <= significance_level) & (data['LMQ'] == 4), 'LMIType'] = 'HL'

        return data

    def handleOutput(self, parameters, context, data, temp_path, input_layer, randExt):
        try:
            out_path = os.path.join(tempfile.gettempdir(), f'temp_lmi_{randExt}.shp')
            data.to_file(out_path)
            vector_layer = QgsVectorLayer(out_path, "Local Morans I", "ogr")
            vector_layer.setCrs(input_layer.crs())

            (sink, dest_id) = self.parameterAsSink(parameters, self.OUTPUT, context, vector_layer.fields(), vector_layer.wkbType(), vector_layer.sourceCrs())
            for feature in vector_layer.getFeatures():
                sink.addFeature(feature, QgsFeatureSink.FastInsert)

            self.dest_id = dest_id
        except Exception as e:
            QgsMessageLog.logMessage(f"Error handling output: {str(e)}", 'Local Morans I', Qgis.Critical)

    def postProcessAlgorithm(self, context, feedback):
        try:
            currentPath = os.path.dirname(__file__)
            processed_layer = QgsProcessingUtils.mapLayerFromString(self.dest_id, context)

            style_file = 'LocalMoransPoints.qml' if processed_layer.geometryType() == 0 else 'LocalMoransPolygons.qml'
            processed_layer.loadNamedStyle(os.path.join(currentPath, 'styles', style_file))

            return {self.OUTPUT: self.dest_id}
        except Exception as e:
            QgsMessageLog.logMessage(f"Error in post-processing: {str(e)}", 'Local Morans I', Qgis.Critical)
            return {self.OUTPUT: self.dest_id}

    def name(self):
        return "Local Moran's I"

    def displayName(self):
        return self.tr(self.name())

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return 'Spatial Analysis'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)
    
    def shortHelpString(self):
        return ("Local Moran's I. \n"
                "There are three available methods:\n"
                "- Queen contiguity: areas with common edges or corners are neighbors (polygons only).\n"
                "- K Nearest Neighbors: works with point/polygon* layers.\n"
                "- Distance Band: areas or points within a fixed distance are neighbors (point/polygon* layers).\n"
                "*For KNN and Distance Band, Moran's I for polygons is based on centroids.")

    def createInstance(self):
        return LocalMoransI()
    
    def icon(self):
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath, 'styles', 'icon.png'))