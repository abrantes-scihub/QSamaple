from qgis.core import (QgsProcessing,
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
                      QgsPointXY,
                      )
from PyQt5.QtCore import QCoreApplication
import os
import tempfile
import random
import string
import geopandas as gpd
import pandas as pd
import numpy as np
import logging
from sklearn.cluster import KMeans
from qgis.PyQt.QtCore import QVariant

class MultivariateClustering(QgsProcessingAlgorithm):
    """
    Multivariate Clustering
    """
    logger = logging.getLogger(__name__)

    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'
    ANALYSIS_FIELDS = 'ANALYSIS_FIELDS'
    CLUSTERING_METHOD = 'CLUSTERING_METHOD'
    INITIALIZATION_METHOD = 'INITIALIZATION_METHOD'
    NUM_CLUSTERS = 'NUM_CLUSTERS'
    MASK_LAYER = 'MASK_LAYER'
    OUTPUT_EVALUATION_TABLE = 'OUTPUT_EVALUATION_TABLE'

    def __init__(self):
        self.dest_id = None
        self.eval_table_id = None
        super().__init__()

        # Initialize logger
        self.configure_logging()

        # Add a custom log category for your algorithm
        self.logger.info('Initializing Multivariate Clustering plugin')


    def configure_logging(self):
        # Configure logging settings
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


    def initAlgorithm(self, config):
        self.addParameter(QgsProcessingParameterVectorLayer(self.INPUT, 'Input layer', types=[QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorPoint], defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, 'Clustered Layer', createByDefault=True, supportsAppend=False, defaultValue=None))
        self.addParameter(QgsProcessingParameterField(self.ANALYSIS_FIELDS, 'Analysis Fields', type=QgsProcessingParameterField.Numeric, parentLayerParameterName=self.INPUT, allowMultiple=True))
        self.addParameter(QgsProcessingParameterEnum(self.CLUSTERING_METHOD, 'Clustering Method', options=['K means'], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(self.INITIALIZATION_METHOD, 'Initialization Method', options=['Optimized seed locations'], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(self.NUM_CLUSTERS, 'Number of Clusters', type=QgsProcessingParameterNumber.Double, defaultValue=None, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(self.MASK_LAYER, 'Mask layer', types=[QgsProcessing.TypeVectorPolygon], optional=True, defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT_EVALUATION_TABLE, 'Output Table for Evaluating Number of Clusters', createByDefault=False, supportsAppend=False, defaultValue=None))

    def processAlgorithm(self, parameters, context, feedback):
        dest_id = None
        dest_eval_id = None

        try:
            input_layer, analysis_fields, clustering_method, initialization_method, num_clusters, mask_layer = self.extractParameters(parameters, context)

            # Print log messages to the Log Messages panel
            QgsMessageLog.logMessage('Starting Multivariate Clustering algorithm', 'Multivariate Clustering', level=Qgis.Info)

            # Extract data from the input layer
            data = self.prepareData(input_layer, analysis_fields)

            if mask_layer:
                # Mask the data using each analysis field
                for field in analysis_fields:
                    data = self.maskData(data, mask_layer, field, context)

            if num_clusters is not None and num_clusters > 0:
                # Use the provided number of clusters
                num_clusters = int(num_clusters)
            else:
                # Evaluate the optimal number of clusters
                num_clusters = self.determineOptimalClusters(data, analysis_fields, initialization_method, feedback)

            # Perform K-means clustering
            clustered_data = self.fitKMeans(data, num_clusters, initialization_method, analysis_fields)

            # Calculate Calinski-Harabasz pseudo F-statistic
            ch_score = self.calculateCalinskiHarabaszPseudoFStatistic(clustered_data, analysis_fields, feedback)

            # Print F-statistic value to the Log Messages panel
            QgsMessageLog.logMessage(f'Calinski-Harabasz pseudo F-statistic: {ch_score}', 'Multivariate Clustering', level=Qgis.Info)

            # Handle Output
            dest_id = self.handleOutput(parameters, context, clustered_data, tempfile.gettempdir(), input_layer)

            # Create and return the evaluation table
            evaluation_table = self.evaluateNumberOfClusters(data, analysis_fields, initialization_method)
            dest_eval_id = self.handleEvaluationTable(parameters, context, evaluation_table, tempfile.gettempdir())

        except Exception as e:
            # Print exception messages to the Log Messages panel
            QgsMessageLog.logMessage(f'An error occurred: {e}', 'Multivariate Clustering', level=Qgis.Critical)

        # Always return the output layers, even if an exception occurred
        return {self.OUTPUT: dest_id, self.OUTPUT_EVALUATION_TABLE: dest_eval_id}

    def extractParameters(self, parameters, context):
        input_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        analysis_fields = self.parameterAsFields(parameters, self.ANALYSIS_FIELDS, context)
        clustering_method = self.parameterAsEnum(parameters, self.CLUSTERING_METHOD, context)
        initialization_method = self.parameterAsEnum(parameters, self.INITIALIZATION_METHOD, context)
        num_clusters = self.parameterAsDouble(parameters, self.NUM_CLUSTERS, context)
        mask_layer = self.parameterAsVectorLayer(parameters, self.MASK_LAYER, context)

        return input_layer, analysis_fields, clustering_method, initialization_method, num_clusters, mask_layer

    def prepareData(self, input_layer, analysis_fields):
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
            self.logger.error(f"An error occurred during data preparation: {e}")
            raise

    def qgisVectorLayerToGeoDataFrame(self, input_layer):
        try:
            fields = input_layer.fields()
            field_names = [field.name() for field in fields]

            data = {field_name: [feature[field_name] for feature in input_layer.getFeatures()] for field_name in field_names}

            geometry = [feature.geometry().asWkt() for feature in input_layer.getFeatures()]
            data['geometry'] = geometry

            gdf = gpd.GeoDataFrame(data, geometry=gpd.array.from_wkt(geometry), crs=input_layer.crs().toProj4())
            return gdf

        except Exception as e:
            logging.exception(f"An error occurred during data preparation: {e}")
            raise

    def maskData(self, data, mask_layer, field, context):
        try:
            # Convert mask layer to GeoDataFrame
            mask_data = self.qgisVectorLayerToGeoDataFrame(mask_layer)

            # Perform spatial join to filter data based on mask layer
            masked_data = gpd.overlay(data, mask_data, how='intersection')

            # Keep only the original columns from the input data
            masked_data = masked_data[[field] + ['geometry']]

            return masked_data
        
        except Exception as e:
            QgsMessageLog.logMessage(f"Error masking data: {str(e)}", 'Local Morans I', Qgis.Critical)
            return None

    def determineOptimalClusters(self, data, analysis_fields, initialization_method, feedback=None):
        clustering_results = self.evaluateNumberOfClusters(data, analysis_fields, initialization_method)

        # Retrieve the optimal number of clusters based on the pseudo F-statistic
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

        evaluation_table = pd.DataFrame(list(clustering_results.items()), columns=['Number of Clusters', 'Pseudo F-statistic'])

        return evaluation_table

    def fitKMeans(self, data, num_clusters, initialization_method, analysis_fields):
        if num_clusters <= 0:
            raise ValueError("The number of clusters must be greater than zero.")

        kmeans = KMeans(n_clusters=num_clusters, init='k-means++' if initialization_method == 0 else 'random',
                        random_state=42)
        data['Cluster'] = kmeans.fit_predict(data[analysis_fields].values)

        self.logger.info(f"K-means clustering completed with {num_clusters} clusters.")

        return data

    def calculateCalinskiHarabaszPseudoFStatistic(self, clustered_data, analysis_fields, feedback=None):
        X = clustered_data[analysis_fields].values
        labels = clustered_data['Cluster'].values

        cluster_means = np.array(
            [X[labels == cluster].mean(axis=0) for cluster in np.unique(labels)])
        overall_mean = X.mean(axis=0)

        between_var = np.sum(
            [np.sum(np.sum((cluster_means[i] - overall_mean) ** 2)) for i in range(len(cluster_means))])
        within_var = np.sum(
            [np.sum(np.sum((X[labels == cluster] - cluster_means[i]) ** 2)) for i, cluster in enumerate(np.unique(labels))])

        ch_score = (between_var / (len(np.unique(labels)) - 1)) / (
                within_var / (len(X) - len(np.unique(labels))))

        if feedback:
            feedback.pushInfo(f"Calinski-Harabasz pseudo F-statistic: {ch_score}")

        return ch_score

    def selectOptimalNumberOfClusters(self, clustering_results):
        # Find the number of clusters with the highest Calinski-Harabasz pseudo F-statistic
        best_num_clusters = clustering_results['Number of Clusters'].iloc[clustering_results['Pseudo F-statistic'].idxmax()]

        return best_num_clusters

    def handleEvaluationTable(self, parameters, context, evaluation_table, temp_path):
        try:
            source_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)

            # Create a new QgsFields object for the output layer
            fields = QgsFields()
            fields.append(QgsField('Number of Clusters', QVariant.Int))
            fields.append(QgsField('Pseudo F-statistic', QVariant.Double))

            # Create the sink for the output layer
            (sink, dest_id) = self.parameterAsSink(parameters, 'OUTPUT_EVALUATION_TABLE', context, fields,
                                                  QgsWkbTypes.Point, source_layer.crs())

            # Iterate over the rows in the evaluation table and add features to the sink
            for index, row in evaluation_table.iterrows():
                feature = QgsFeature(fields)
                feature['Number of Clusters'] = int(row['Number of Clusters'])
                feature['Pseudo F-statistic'] = float(row['Pseudo F-statistic'])

                # Create a QgsGeometry for the point based on the Number of Clusters
                point = QgsGeometry.fromPointXY(QgsPointXY(float(row['Number of Clusters']),
                                                            float(row['Pseudo F-statistic'])))
                feature.setGeometry(point)

                # Add the feature to the sink
                sink.addFeature(feature)

            return dest_id
        
        except Exception as e:
            QgsMessageLog.logMessage(f'Error handling evaluation table: {e}', 'Multivariate Clustering',
                                     level=Qgis.Critical)
            return self.OUTPUT_EVALUATION_TABLE

    def handleOutput(self, parameters, context, data, temp_path, input_layer):
        try:
            rand_ext = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
            out_path = os.path.join(tempfile.gettempdir(), f'temp_clustered_{rand_ext}.shp')

            # Save GeoDataFrame to a shapefile
            data.to_file(out_path)

            # Create a QgsVectorLayer using the saved shapefile
            vector_layer = QgsVectorLayer(out_path, "Clustered Layer", "ogr")
            vector_layer.setCrs(input_layer.crs())

            # Use the source layer's fields when creating the sink
            fields = input_layer.fields()

            # Add the 'Cluster' field to the fields
            fields.append(QgsField('Cluster', QVariant.Int))

            (sink, dest_id) = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                                  QgsWkbTypes.Point, input_layer.crs())

            # Add features directly from the vector layer
            for feature in vector_layer.getFeatures():
                sink.addFeature(feature)

            return dest_id
        
        except Exception as e:
            QgsMessageLog.logMessage(f'Error handling output layer: {e}', 'Multivariate Clustering', level=Qgis.Critical)
            return self.OUTPUT

    def name(self):
        return 'Multivariate Clustering'

    def displayName(self):
        """
        Returns the translated algorithm name, which should be used for any
        user-visible display of the algorithm name.
        """
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
        from qgis.PyQt.QtGui import QIcon
        import os
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath, 'styles', 'icon.png'))