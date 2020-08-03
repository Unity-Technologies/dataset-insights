import argparse
import os

import dash_core_components as dcc
import dash_html_components as html
from dash.dependencies import Input, Output

import datasetinsights.visualization.overview as overview
from datasetinsights.visualization.app import get_app
from datasetinsights.visualization.object_detection import (
    render_object_detection_layout,
)

app = get_app()


def main_layout(data_root):
    """ Method for generating main app layout.
    Args:
        data_root(str): path to the dataset.
    Returns:
        html layout: main layout design with tabs for overview statistics
            and object detection.
    """
    app_layout = html.Div(
        [
            html.H1(
                children="Dataset Insights",
                style={
                    "textAlign": "center",
                    "padding": 20,
                    "background": "lightgrey",
                },
            ),
            html.Div(
                [
                    dcc.Tabs(
                        id="page_tabs",
                        value="dataset_overview",
                        children=[
                            dcc.Tab(
                                label="Overview", value="dataset_overview",
                            ),
                            dcc.Tab(
                                label="Object Detection",
                                value="object_detection",
                            ),
                        ],
                    ),
                    html.Div(id="main_page_tabs"),
                ]
            ),
        ]
    )
    return app_layout


@app.callback(
    Output("main_page_tabs", "children"), [Input("page_tabs", "value")]
)
def render_content(tab):
    if tab == "dataset_overview":
        return overview.html_overview(data_root)
    elif tab == "object_detection":
        return render_object_detection_layout(data_root)


def check_path(path):
    if os.path.isdir(path):
        return path
    else:
        raise ValueError(f"Path {path} not found")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", help="Path to the data root")
    args = parser.parse_args()
    data_root = check_path(args.data_root)
    app.layout = main_layout(data_root)
    app.run_server(debug=True)
