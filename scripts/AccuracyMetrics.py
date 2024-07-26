from qgis.core import (QgsProcessing,
                      QgsProcessingAlgorithm,
                      QgsProcessingParameterVectorLayer,
                      QgsProcessingParameterField,
                      QgsProcessingParameterFeatureSink,
                      QgsProcessingException,
                      QgsFields,
                      QgsField,
                      QgsWkbTypes,
                      QgsMessageLog,
                      Qgis,
                      QgsFeatureSink,
                      QgsVectorLayer,
                      QgsFeature,
                      QgsGeometry,
                      QgsProject)

from PyQt5.QtCore import QCoreApplication, QVariant
import os
import tempfile
import geopandas as gpd
import numpy as np
import pandas as pd
import random
import string
from qgis.PyQt.QtGui import QIcon

class AccuracyMetrics(QgsProcessingAlgorithm):
    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'
    MEASURED_VALUE = 'MEASURED_DATA'
    ESTIMATED_VALUE = 'ESTIMATED_DATA'
    CASE_FIELD = 'CASE_FIELD'

    def initAlgorithm(self, config):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT,
            self.tr('Input Vector Layer'),
            types=[QgsProcessing.TypeVectorPoint, QgsProcessing.TypeVectorPolygon]
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT,
            self.tr('Accuracy'),
            createByDefault=True,
            supportsAppend=False
        ))
        self.addParameter(QgsProcessingParameterField(
            self.MEASURED_VALUE,
            self.tr('Measured Data Field'),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric
        ))
        self.addParameter(QgsProcessingParameterField(
            self.ESTIMATED_VALUE,
            self.tr('Estimated Data Field'),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric
        ))
        self.addParameter(QgsProcessingParameterField(
            self.CASE_FIELD,
            self.tr('Case Field'),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Any,
            optional=True
        ))

    def processAlgorithm(self, parameters, context, feedback):
        # Extract parameters
        layer_source, estimated_field, measured_field, case_field = self.extractParameters(parameters, context)

        # Prepare data
        data = self.prepareData(layer_source, estimated_field, measured_field, case_field)
        
        # Debugging: Check the prepared data
        QgsMessageLog.logMessage(f"Prepared Data:\n{data.head()}", 'Accuracy Metrics', Qgis.Info)
        
        # Calculate errors
        data = AccuracyMetricsUtils.calculateError(data, estimated_field, measured_field)
        data = AccuracyMetricsUtils.calculateAbsoluteError(data)
        data = AccuracyMetricsUtils.calculateRelativeError(data, measured_field)
        data = AccuracyMetricsUtils.calculateAbsoluteRelativeError(data)
        
        # Debugging: Check intermediate results
        QgsMessageLog.logMessage(f"Data after error calculations:\n{data.head()}", 'Accuracy Metrics', Qgis.Info)
        
        if case_field:
            # Grouped calculations
            data = AccuracyMetricsUtils.calculateMeanAbsoluteError(data, case_field)
            data = AccuracyMetricsUtils.calculateMSE(data, estimated_field, measured_field, case_field)
            data = AccuracyMetricsUtils.calculateRMSE(data, estimated_field, measured_field, case_field)
            data = AccuracyMetricsUtils.calculateSMAPE(data, estimated_field, measured_field, case_field)

        else:
            # Non-grouped calculations
            data = AccuracyMetricsUtils.calculateMeanAbsoluteError(data)
            data = AccuracyMetricsUtils.calculateMSE(data, estimated_field, measured_field)
            data = AccuracyMetricsUtils.calculateRMSE(data, estimated_field, measured_field)
            data = AccuracyMetricsUtils.calculateSMAPE(data, estimated_field, measured_field)

        # Debugging: Check final results
        QgsMessageLog.logMessage(f"Final Data:\n{data.head()}", 'Accuracy Metrics', Qgis.Info)

        # Handle output
        dest_id = self.handleOutput(data, parameters, context, layer_source)
        return {self.OUTPUT: dest_id}

    def extractParameters(self, parameters, context):
        layer_source = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if not layer_source:
            raise QgsProcessingException(self.tr("Invalid input layer"))

        measured_field = self.parameterAsString(parameters, self.MEASURED_VALUE, context)
        estimated_field = self.parameterAsString(parameters, self.ESTIMATED_VALUE, context)
        case_field = self.parameterAsString(parameters, self.CASE_FIELD, context)

        return layer_source, estimated_field, measured_field, case_field

    def prepareData(self, layer_source, estimated_field, measured_field, case_field):
        if not layer_source.isValid():
            raise QgsProcessingException(self.tr("Invalid input layer"))

        data = self.qgisVectorLayerToGeoDataFrame(layer_source)
        QgsMessageLog.logMessage(f"Data structure: {data.head()}", 'Accuracy Metrics', Qgis.Info)
        return data

    def qgisVectorLayerToGeoDataFrame(self, layer_source):
        try:
            data = {field.name(): [feature[field.name()] for feature in layer_source.getFeatures()]
                    for field in layer_source.fields()}
            geometry = [feature.geometry().asWkt() for feature in layer_source.getFeatures()]
            data['geometry'] = geometry
            gdf = gpd.GeoDataFrame(data, geometry=gpd.GeoSeries.from_wkt(geometry), crs=layer_source.crs().toWkt())
            return gdf
        except Exception as e:
            raise QgsProcessingException(self.tr(f"Error converting layer to GeoDataFrame: {e}"))

    def handleOutput(self, data, parameters, context, layer_source):
        # Create temporary output path
        rand_ext = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        out_path = os.path.join(tempfile.gettempdir(), f'temp_output{rand_ext}.shp')

        # Save GeoDataFrame to file
        data.to_file(out_path)

        # Create Accuracy
        vector_layer = QgsVectorLayer(out_path, "Accuracy Metrics", "ogr")
        if not vector_layer.isValid():
            raise QgsProcessingException(self.tr("Failed to create output layer"))

        # Prepare fields
        new_fields = QgsFields()
        for field in layer_source.fields():
            new_fields.append(field)

        error_fields = [
            QgsField('Error', QVariant.Double),
            QgsField('ABSE', QVariant.Double),
            QgsField('RELE', QVariant.Double),
            QgsField('ARE', QVariant.Double),
            QgsField('MAE', QVariant.Double),
            QgsField('MSE', QVariant.Double),
            QgsField('RMSE', QVariant.Double),
            QgsField('SMAPE', QVariant.Double)
        ]

        for field in error_fields:
            new_fields.append(field)

        case_field = self.parameterAsString(parameters, self.CASE_FIELD, context)
        if case_field and not new_fields.indexOf(case_field):
            new_fields.append(QgsField(case_field, QVariant.String))

        # Create sink
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, new_fields,
            layer_source.wkbType(), layer_source.crs()
        )

        # Add features to sink
        for feature in vector_layer.getFeatures():
            attrs = feature.attributes()
            if case_field:
                case_value = data.at[feature.id(), case_field]
                if not pd.isna(case_value):
                    attrs.append(str(case_value))
            feature.setAttributes(attrs)
            sink.addFeature(feature, QgsFeatureSink.FastInsert)

        return dest_id

    def name(self):
        return 'accuracymetrics'

    def displayName(self):
        return self.tr('Accuracy Metrics')

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return 'Accuracy'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def shortHelpString(self):
        return ("Calculates various accuracy metrics, including error measures. "
                "Specify a case field to calculate metrics per class.")

    def createInstance(self):
        return AccuracyMetrics()

    def icon(self):
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath, 'styles', 'icon.png'))

class AccuracyMetricsUtils:
    @staticmethod
    def calculateError(data, estimated_field, measured_field):
        data['Error'] = data[estimated_field] - data[measured_field]
        return data

    @staticmethod
    def calculateAbsoluteError(data):
        data['ABSE'] = np.abs(data['Error'])
        return data

    @staticmethod
    def calculateRelativeError(data, measured_field):
        data['RELE'] = data['Error'] / data[measured_field].replace(0, np.nan)
        return data

    @staticmethod
    def calculateAbsoluteRelativeError(data):
        data['ARE'] = np.abs(data['RELE'])
        return data

    @staticmethod
    def calculateMeanAbsoluteError(data, case_field=None):
        if case_field:
            grouped_data = data.groupby(case_field)['ABSE'].mean().reset_index()
            grouped_data = grouped_data.rename(columns={'ABSE': 'MAE'})
            data = pd.merge(data, grouped_data, on=case_field, how='left')
        else:
            data['MAE'] = data['ABSE'].mean()

        return data
    
    @staticmethod
    def calculateMSE(data, estimated_field, measured_field, case_field=None):
        if case_field:
            grouped_data = data.groupby(case_field).apply(
                lambda group: ((group[estimated_field] - group[measured_field]) ** 2).mean()
            ).reset_index(name='MSE')
            data = pd.merge(data, grouped_data, on=case_field, how='left')
        else:
            data['MSE'] = ((data[estimated_field] - data[measured_field]) ** 2).mean()
        return data
    
    @staticmethod
    def calculateRMSE(data, estimated_field, measured_field, case_field=None):
        if case_field:
            grouped_data = data.groupby(case_field).apply(
                lambda group: np.sqrt(((group[estimated_field] - group[measured_field]) ** 2).mean())
            ).reset_index(name='RMSE')
            data = pd.merge(data, grouped_data, on=case_field, how='left')
        else:
            data['RMSE'] = np.sqrt(((data[estimated_field] - data[measured_field]) ** 2).mean())
        return data

    @staticmethod
    def calculateSMAPE(data, estimated_field, measured_field, case_field=None):
        def smape(a, f):
            return 100 * np.mean(2 * np.abs(f - a) / (np.abs(a) + np.abs(f)))
        
        if case_field:
            grouped_data = data.groupby(case_field).apply(
                lambda group: smape(group[estimated_field], group[measured_field])
            ).reset_index(name='SMAPE')
            data = pd.merge(data, grouped_data, on=case_field, how='left')
        else:
            data['SMAPE'] = smape(data[estimated_field], data[measured_field])
        
        return data