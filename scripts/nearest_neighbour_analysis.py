import os
import math

import plotly as plt
import plotly.graph_objs as go
from plotly import tools
from qgis.PyQt.QtCore import QUrl, QCoreApplication
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout

from qgis.core import (QgsProcessingAlgorithm, QgsFeatureRequest, QgsFeature,
                       QgsDistanceArea, QgsProject, QgsProcessing,
                       QgsProcessingException, QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterFileDestination, QgsProcessingParameterNumber,
                       QgsProcessingOutputNumber, QgsProcessingParameterExtent,
                       QgsSpatialIndex)

import webbrowser

class WebDialog(QDialog):
    def __init__(self, parent=None, title='WebDialog'):
        super().__init__(parent=parent)
        self.setWindowTitle(title)
        self.html_file = None

    def setHTML(self, file_path):
        self.html_file = file_path
        # Open the HTML file in the default web browser
        webbrowser.open_new_tab(file_path)


class NearestNeighbourAnalysis(QgsProcessingAlgorithm):
    INPUT = 'INPUT'
    OUTPUT_HTML_FILE = 'OUTPUT_HTML_FILE'
    K = 'K'
    EXTENT = 'EXTENT'

    def __init__(self):
        super().__init__()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, self.tr('Points'), [QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterNumber(self.K, self.tr('Number of nearest neighbours (k)'), defaultValue=10))
        self.addParameter(QgsProcessingParameterExtent(self.EXTENT, self.tr('Area of the analysis (A)'), optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(self.OUTPUT_HTML_FILE, self.tr('Nearest Neighbour Analysis'), self.tr('HTML files (*.html)'), None, True))

    def postProcessAlgorithm(self, context, feedback, parent=None):
        dial = WebDialog(parent, self.displayName())
        dial.setHTML(self.path)
        dial.show()
        return self.output

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        A = source.sourceExtent()
        A = float(A.width() * A.height())

        extent = self.parameterAsExtent(parameters, self.EXTENT, context, source.sourceCrs())
        if extent.area() != 0:
             A = float(extent.width() * extent.height())

        k = self.parameterAsInt(parameters, self.K, context)

        output_file = self.parameterAsFileOutput(parameters, self.OUTPUT_HTML_FILE, context)
        self.path = output_file

        spatialIndex = QgsSpatialIndex(source, feedback)

        count = source.featureCount()
        features = source.getFeatures()
        total = 100.0 / count if count else 1

        nnDistances = {}
        x_values = []
        for i in range(0, k):
            nnDistances[i+1] = []
            x_values.append(i+1)

        distance = QgsDistanceArea()
        distance.setSourceCrs(source.sourceCrs(), context.transformContext())
        distance.setEllipsoid(context.project().ellipsoid())
        for current, feat in enumerate(features):
            if feedback.isCanceled():
                break

            neighbours = spatialIndex.nearestNeighbor(feat.geometry().asPoint(), k+1)
            for i in range(0, k):
                neighbour = QgsFeature()

                if i+1 < len(neighbours):
                    source.getFeatures(QgsFeatureRequest(neighbours[i+1])).nextFeature(neighbour)
                    dist = distance.measureLine(neighbour.geometry().asPoint(),
                                                feat.geometry().asPoint())
                    nnDistances[i+1].append(dist)
            feedback.setProgress(int(current * total))

        results = {}
        do_values = []
        de_values = []
        de_max_values = []
        de_min_values = []
        nni_values = []

        for k, distances in nnDistances.items():
            if len(distances) == 0:
                x_values = x_values[:k-1]
                break
            do = float(sum(distances)) / count
            if k == 0:
                de = float(0.5 / math.sqrt(count / A))
            else:
                de = (k*math.factorial(2*k))/(math.pow((math.pow(2,k)*math.factorial(k)),2)*math.sqrt(count / A))
            nni = float(do / de)
            SE = float(0.26136 / math.sqrt(count ** 2 / A))
            do_values.append(round(do))
            de_values.append(round(de))
            de_max_values.append(round(de+SE))
            de_min_values.append(round(de-SE))
            nni_values.append(round(nni,3))
            result = {}
            result['K'] = k
            result['OBSERVED_MD'] = do
            result['EXPECTED_MD'] = de
            result['NN_INDEX'] = nni
            result['POINT_COUNT'] = count
            if k == 0:
                result['Z_SCORE'] = float((do - de) / SE)
            results[k] = result

        feedback.pushInfo('Results: {}'.format(results))

        if not feedback.isCanceled():
            do_data = go.Scatter(x=x_values,
                               y=do_values,
                               mode='lines+markers',
                               name = self.tr('Observed mean distance'))
            de_data = go.Scatter(x=x_values,
                                y=de_values,
                                mode='lines+markers',
                                marker = dict(
                                    size = 5,
                                    color = 'rgba(120, 120, 120, .8)',
                                    line = dict(
                                        width = 0,
                                        color = 'rgb(0, 0, 0)'
                                    )
                                ),
                                name = self.tr('Expected distance (if random)'))
            x_rev = x_values[::-1]
            de_min_values = de_min_values[::-1]
            se_data = go.Scatter(
                                x=x_values+x_rev,
                                y=de_max_values+de_min_values,
                                fill='tozerox',
                                fillcolor='rgba(120, 120, 120, .3)',
                                line=dict(color='rgba(255,255,255,0)'),
                                showlegend=False,
                                name=self.tr('Expected distance (SE)'),
                            )
            nni_data = go.Scatter(x=x_values,
                               y=nni_values,
                               mode='lines+markers',
                               name = self.tr('Observed mean distance'),
                               showlegend=False,)

            fig = tools.make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1)
            fig.append_trace(do_data, 1, 1)
            fig.append_trace(de_data, 1, 1)
            fig.append_trace(se_data, 1, 1)
            fig.append_trace(nni_data, 2, 1)

            fig['layout'].update(
                                title= self.tr('K-Nearest neighbours'),
                                hovermode= 'x',

                                xaxis= dict(
                                    title= self.tr('K-Order'),
                                    ticklen= 5,
                                    zeroline= False,
                                    range=[0.9, len(x_values)+0.1]
                                ),
                                yaxis=dict(
                                    title= self.tr('Distance [m]'),
                                    ticklen= 5,
                                    gridwidth= 1,
                                ),
                                yaxis2=dict(
                                    title= self.tr('NN Index'),
                                    ticklen= 5,
                                    gridwidth= 1,
                                    range=[0, 1 if max(nni_values) < 1 else max(nni_values)+0.1]
                                ),
                                showlegend= True,
                                legend=dict(orientation="h", y=1)
                            )

            plt.offline.plot(fig, filename=output_file, auto_open=False)

        self.output = {self.OUTPUT_HTML_FILE: output_file}
        return self.output

    def name(self):
        return 'Nearest Neighbour Analysis'

    def displayName(self):
        return self.tr(self.name())

    def group(self):
        return self.tr(self.groupId())

    def groupId(self):
        return 'Spatial Analysis'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def shortHelpString(self):
        return ("Nearest Neighbour Analysis. \n"
                "Performs Nearest Neighbour analysis. \n")

    def createInstance(self):
        return NearestNeighbourAnalysis()

    def icon(self):
        from qgis.PyQt.QtGui import QIcon
        pluginPath = os.path.dirname(__file__)
        return QIcon(os.path.join(pluginPath, 'styles', 'icon.png'))
