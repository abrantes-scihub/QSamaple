# SAMAPLE Toolbox

[![QGIS Version](https://img.shields.io/badge/QGIS-3.x-brightgreen)](https://qgis.org/)

## Description

A user-friendly tool for semi-automatic mapping and accuracy analysis  in plot-level experiments within QGIS environment.

## Features

- SAMPLE Toolbox comprises a set of tools for spatial analysis and accuracy measurements combined to design the **samaple model** - a workflow that performs the mapping and accuracy measurements in a semi-automatic manner.

- Spatial Analysis
    - *Local Moran's I*: it was designed to calculate Local Moran's I, a spatial autocorrelation statistic, for analyzing spatial patterns and outliers locations.

    - *Multivariate Clustering*: it uses the K-means clustering method to cluster features based on user-selected numeric analysis fields.
    If the user does not specify the number of clusters, the algorithm evaluates the optimal number of clusters using the Calinski-Harabasz pseudo F-statistic.

- Accuracy Metrics

- Interpolation: Natural Neighbour.

## Installation

1. Open QGIS.
2. Go to the <code>Plugins</code> menu and select <code>Manage and Install Plugins</code>.
3. In the <code>Plugins</code> dialog, click on the <code>Install from ZIP</code> button.
4. Select the ZIP file of your plugin and click <code>Install Plugin</code>.
5. Once installed, enable the plugin by checking the corresponding checkbox in the <code>Plugins</code> dialog.

## Usage

Explain how to use your plugin and provide any necessary instructions or examples.

## Contributing

If you would like to contribute to this project, please follow the guidelines in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

This project is licensed under the [MIT License](LICENSE).

## Contact

- Provide contact information for support or inquiries.
