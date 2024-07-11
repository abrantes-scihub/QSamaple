from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsVectorLayer,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsMessageLog,
    Qgis,
    QgsFeature,
    QgsGeometry,
    QgsCoordinateReferenceSystem
)
from PyQt5.QtCore import QCoreApplication
import os
import tempfile
import random
import string
import geopandas as gpd
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QIcon


class MultivariateClustering(QgsProcessingAlgorithm):
    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'
    ANALYSIS_FIELDS = 'ANALYSIS_FIELDS'
    CLUSTERING_METHOD = 'CLUSTERING_METHOD'
    INITIALIZATION_METHOD = 'INITIALIZATION_METHOD'
    NUM_CLUSTERS = 'NUM_CLUSTERS'
    MASK_LAYER = 'MASK_LAYER'
    OUTPUT_EVALUATION_TABLE = 'OUTPUT_EVALUATION_TABLE'

    def __init__(self):
        super().__init__()

    def initAlgorithm(self, config):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT, 'Input layer', types=[QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorPoint], defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, 'Clustered Layer', createByDefault=True, supportsAppend=False, defaultValue=None))
        self.addParameter(QgsProcessingParameterField(
            self.ANALYSIS_FIELDS, 'Analysis Fields', type=QgsProcessingParameterField.Numeric, parentLayerParameterName=self.INPUT, allowMultiple=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.CLUSTERING_METHOD, 'Clustering Method', options=['K means'], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.INITIALIZATION_METHOD, 'Initialization Method', options=['Optimized seed locations'], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.NUM_CLUSTERS, 'Number of Clusters', type=QgsProcessingParameterNumber.Double, defaultValue=None, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.MASK_LAYER, 'Mask layer', types=[QgsProcessing.TypeVectorPolygon], optional=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_EVALUATION_TABLE, 'Output Table for Evaluating Number of Clusters', createByDefault=False, supportsAppend=False, defaultValue=None))

    def processAlgorithm(self, parameters, context, feedback):
        dest_id = None
        dest_eval_id = None

        try:
            input_layer, analysis_fields, clustering_method, initialization_method, num_clusters, mask_layer = self.extractParameters(parameters, context)

            QgsMessageLog.logMessage('Starting Multivariate Clustering algorithm', 'Multivariate Clustering', level=Qgis.Info)

            data = self.prepareData(input_layer, analysis_fields, context)
            QgsMessageLog.logMessage(f'Data before masking: {data}', 'Multivariate Clustering', level=Qgis.Info)

            if mask_layer:
                masked_data = self.maskData(data, mask_layer, analysis_fields, context)
            else:
                masked_data = data

            if num_clusters is not None and num_clusters > 0:
                num_clusters = int(num_clusters)
            else:
                num_clusters = self.determineOptimalClusters(masked_data, analysis_fields, initialization_method, feedback)

            clustered_data = self.fitKMeans(masked_data, num_clusters, initialization_method, analysis_fields)

            ch_score = self.calculateCalinskiHarabaszPseudoFStatistic(clustered_data, analysis_fields, feedback)
            QgsMessageLog.logMessage(f'Calinski-Harasz pseudo F-statistic: {ch_score}', 'Multivariate Clustering', level=Qgis.Info)

            dest_id = self.handleOutput(parameters, context, clustered_data, tempfile.gettempdir(), input_layer, mask_layer)

            evaluation_table = self.evaluateNumberOfClusters(masked_data, analysis_fields, initialization_method)
            dest_eval_id = self.handleEvaluationTable(parameters, context, evaluation_table)

        except Exception as e:
            QgsMessageLog.logMessage(f'An error occurred: {e}', 'Multivariate Clustering', level=Qgis.Critical)

        return {self.OUTPUT: dest_id, self.OUTPUT_EVALUATION_TABLE: dest_eval_id}

    def extractParameters(self, parameters, context):
        input_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        analysis_fields = self.parameterAsFields(parameters, self.ANALYSIS_FIELDS, context)
        clustering_method = self.parameterAsEnum(parameters, self.CLUSTERING_METHOD, context)
        initialization_method = self.parameterAsEnum(parameters, self.INITIALIZATION_METHOD, context)
        num_clusters = self.parameterAsDouble(parameters, self.NUM_CLUSTERS, context)
        mask_layer = self.parameterAsVectorLayer(parameters, self.MASK_LAYER, context)

        return input_layer, analysis_fields, clustering_method, initialization_method, num_clusters, mask_layer

    def prepareData(self, input_layer, analysis_fields, context):
        if not input_layer.isValid():
            raise Exception('Invalid input layer')

        data = self.qgisVectorLayerToGeoDataFrame(input_layer)
        return data

    def qgisVectorLayerToGeoDataFrame(self, input_layer):
        field_names = [field.name() for field in input_layer.fields()]
        data = {field_name: [feature[field_name] for feature in input_layer.getFeatures()] for field_name in field_names}
        geometry = [feature.geometry().asWkt() for feature in input_layer.getFeatures()]
        data['geometry'] = geometry

        return gpd.GeoDataFrame(data, geometry=gpd.array.from_wkt(geometry), crs=input_layer.crs().toProj4())

    def maskData(self, data, mask_layer, analysis_fields, context):
        QgsMessageLog.logMessage(f"Mask layer: {mask_layer.name()}", 'Multivariate Clustering', Qgis.Info)
        QgsMessageLog.logMessage(f"Analysis fields: {analysis_fields}", 'Multivariate Clustering', Qgis.Info)

        # Convert mask layer to GeoDataFrame
        mask_data = self.qgisVectorLayerToGeoDataFrame(mask_layer)
        QgsMessageLog.logMessage(f"Mask data: {mask_data}", 'Multivariate Clustering', Qgis.Info)
        
        # Check if the CRS of both dataframes match
        if data.crs != mask_data.crs:
            QgsMessageLog.logMessage("CRS mismatch: Reprojecting mask data to match data CRS.", 'Multivariate Clustering', Qgis.Info)
            mask_data = mask_data.to_crs(data.crs)

        # Perform intersection
        try:
            masked_data = gpd.overlay(data, mask_data, how='intersection')
            QgsMessageLog.logMessage(f"Masked data after overlay: {masked_data}", 'Multivariate Clustering', Qgis.Info)
        except Exception as e:
            QgsMessageLog.logMessage(f"Error during intersection: {e}", 'Multivariate Clustering', Qgis.Critical)
            raise e

        return masked_data

    def determineOptimalClusters(self, data, analysis_fields, initialization_method, feedback=None):
        clustering_results = self.evaluateNumberOfClusters(data, analysis_fields, initialization_method)

        best_num_clusters = self.selectOptimalNumberOfClusters(clustering_results)

        if feedback:
            feedback.pushInfo(f"Selected optimal number of clusters: {best_num_clusters}")

        return best_num_clusters

    def evaluateNumberOfClusters(self, data, analysis_fields, initialization_method):
        clustering_results = {}
        for num_clusters in range(2, 31):
            clustered_data = self.fitKMeans(data, num_clusters, initialization_method, analysis_fields)
            ch_score = self.calculateCalinskiHarabaszPseudoFStatistic(clustered_data, analysis_fields)
            clustering_results[num_clusters] = ch_score

        return pd.DataFrame(list(clustering_results.items()), columns=['Number of Clusters', 'Pseudo F-statistic'])

    def fitKMeans(self, data, num_clusters, initialization_method, analysis_fields):
        if num_clusters <= 0:
            raise ValueError("The number of clusters must be greater than zero.")

        kmeans = KMeans(n_clusters=num_clusters, init='k-means++' if initialization_method == 0 else 'random', random_state=42)
        data['Cluster'] = kmeans.fit_predict(data[analysis_fields].values)

        return data

    def calculateCalinskiHarabaszPseudoFStatistic(self, clustered_data, analysis_fields, feedback=None):
        X = clustered_data[analysis_fields].values
        labels = clustered_data['Cluster'].values

        cluster_means = np.array([X[labels == cluster].mean(axis=0) for cluster in np.unique(labels)])
        overall_mean = X.mean(axis=0)

        between_var = np.sum([np.sum((cluster_means[i] - overall_mean) ** 2) for i in range(len(cluster_means))])
        within_var = np.sum([np.sum((X[labels == cluster] - cluster_means[i]) ** 2) for i, cluster in enumerate(np.unique(labels))])

        ch_score = (between_var / (len(np.unique(labels)) - 1)) / (within_var / (len(X) - len(np.unique(labels))))

        if feedback:
            feedback.pushInfo(f"Calinski-Harasz pseudo F-statistic: {ch_score}")

        return ch_score

    def selectOptimalNumberOfClusters(self, clustering_results):
        best_num_clusters = clustering_results.iloc[clustering_results['Pseudo F-statistic'].idxmax()]['Number of Clusters']
        return int(best_num_clusters)

    def handleOutput(self, parameters, context, data, temp_path, input_layer, mask_layer=None):
        rand_ext = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        out_path = os.path.join(tempfile.gettempdir(), f'temp_clustered_{rand_ext}.shp')

        # Save the clustered data to a temporary file
        data.to_file(out_path)

        # Load the temporary file into a QGIS vector layer
        vector_layer = QgsVectorLayer(out_path, "Clustered Layer", "ogr")
        vector_layer.setCrs(input_layer.crs())

        # Create a dictionary to map geometries to cluster values
        geometry_to_cluster = {feature.geometry().asWkt(): feature['Cluster'] for feature in vector_layer.getFeatures()}

        # Define the fields for the output layer
        fields = QgsFields()
        for field in input_layer.fields():
            fields.append(field)
        fields.append(QgsField('Cluster', QVariant.Int))

        # Create the output sink
        (sink, dest_id) = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.Point, input_layer.crs())

        # Add features to the output sink
        input_features = input_layer.getFeatures()
        for feature in input_features:
            geometry = feature.geometry()
            geometry_key = geometry.asWkt()

            if geometry_key in geometry_to_cluster:
                cluster_value = geometry_to_cluster[geometry_key]
                new_feature = QgsFeature(fields)
                new_feature.setGeometry(geometry)

                # Preserve attributes only for rows with a cluster number
                attributes = feature.attributes()
                attributes.append(cluster_value)
                new_feature.setAttributes(attributes)

                sink.addFeature(new_feature)

        return dest_id

    def handleEvaluationTable(self, parameters, context, evaluation_table):
        # Define fields for the evaluation table
        fields = QgsFields()
        fields.append(QgsField('Number of Clusters', QVariant.Int))
        fields.append(QgsField('Pseudo F-statistic', QVariant.Double))

        # Create the evaluation table sink
        (sink, dest_eval_id) = self.parameterAsSink(parameters, self.OUTPUT_EVALUATION_TABLE, context, fields, QgsWkbTypes.NoGeometry, QgsCoordinateReferenceSystem('EPSG:4326'))

        # Add rows to the evaluation table sink
        for index, row in evaluation_table.iterrows():
            feature = QgsFeature()
            feature.setFields(fields)
            feature.setAttribute('Number of Clusters', int(row['Number of Clusters']))
            feature.setAttribute('Pseudo F-statistic', float(row['Pseudo F-statistic']))
            sink.addFeature(feature)

        return dest_eval_id

    def name(self):
        return 'Multivariate Clustering'

    def displayName(self):
        return self.tr(self.name())

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return 'Spatial Analysis'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def shortHelpString(self):
        return ("Multivariate Clustering. \n"
                "Performs clustering analysis using K-means algorithm.")

    def createInstance(self):
        return MultivariateClustering()
    def icon(self):
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath, 'styles', 'icon.png'))