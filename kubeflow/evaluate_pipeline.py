import kfp.dsl as dsl
import kfp.gcp as gcp

MAX_GPU = 1
MAX_MEMORY = "16Gi"


@dsl.pipeline(
    name="evaluation pipeline",
    description="evaluate model using kubeflow pipeline",
)
def evaluate_pipeline(
    num_proc: int = 1,
    volume_size: str = "20Gi",
    data_name: str = "GroceriesReal",
    test_split="test",
    config_file: str = "datasetinsights/configs/faster_rcnn_synthetic.yaml",
    logdir: str = "gs://thea-dev/runs/yyyymmdd-hhmm",
    docker_image: str = (
        "gcr.io/unity-ai-thea-test/datasetinsights:<git-comit-sha>"
    ),
    checkpoint_file: str = "",
):
    """Evaluate Pipeline

    This is currently configured as a three-step pipeline. 1) Create
    a persistent volume that can be used to store data. 2) Download
    data for the pipeline. 3) Kick off evaluation jobs.
    """
    # Create large persistant volume to store training data.
    vop = dsl.VolumeOp(
        name="evaluate-pvc",
        resource_name="evaluate-pvc",
        size=volume_size,
        modes=dsl.VOLUME_MODE_RWO,
    )

    # Dataset Download
    download = dsl.ContainerOp(
        name="groceriesreal download",
        image=docker_image,
        command=["python", "-m", "datasetinsights.scripts.public_download"],
        arguments=[f"--name={data_name}"],
        pvolumes={"/data": vop.volume},
    )
    # Memory limit of download run
    download.set_memory_limit(MAX_MEMORY)
    # Use GCP Service Accounts to allow access to GCP resources
    download.apply(gcp.use_gcp_secret("user-gcp-sa"))

    evaluate = dsl.ContainerOp(
        name="evaluate",
        image=docker_image,
        command=[
            "python",
            "-m",
            "torch.distributed.launch",
            f"--nproc_per_node={num_proc}",
        ],
        arguments=[
            "datasetinsights",
            "evaluate",
            f"--config={config_file}",
            f"--tb-log-dir={logdir}",
            f"--checkpoint-file={checkpoint_file}",
        ],
        file_outputs={"mlpipeline-metrics": "/mlpipeline-metrics.json"},
        # Refer to pvloume in previous step to explicitly call out dependency
        pvolumes={"/data": download.pvolumes["/data"]},
    )
    # GPU limit here has to be hard coded integer instead of derived from
    # num_proc, otherwise it will fail kubeflow validation as it will create
    # yaml with palceholder like {{num_proc}}...
    evaluate.set_gpu_limit(MAX_GPU)
    # Request GPUs
    evaluate.add_node_selector_constraint(
        "cloud.google.com/gke-accelerator", "nvidia-tesla-v100"
    )
    # Request master machine with larger memory. Same as gpu limit, this has to
    # be hard coded constants.
    evaluate.set_memory_limit(MAX_MEMORY)
    # Use GCP Service Accounts to allow access to GCP resources
    evaluate.apply(gcp.use_gcp_secret("user-gcp-sa"))


if __name__ == "__main__":
    import kfp.compiler as compiler

    compiler.Compiler().compile(evaluate_pipeline, __file__ + ".tar.gz")
