from datasetinsights.storage.gcs import copy_folder_to_gcs
from .base import Estimator
import datasetinsights.constants as const
from datasetinsights.datasets import Dataset
from datasetinsights.evaluation_metrics import EvaluationMetric
import logging
import numpy as np
import os
from pathlib import Path

import glob

import tensorflow as tf
from tensorflow.python.framework import ops
from tensorflow.keras import layers
from tensorflow.keras.applications import VGG16
from keras import optimizers
from keras.preprocessing import image
from keras.applications.vgg16 import preprocess_input
from tensorflow.keras.layers import Input


logger = logging.getLogger(__name__)

GCS_PATH_MODELS = "gs://thea-dev/runs/single-cube/"
LOCAL_UNZIP_IMAGES = "single_cube/ScreenCapture"


def linear_normalized(x):
    """ Create a activation function for the orientation model.
    The prediction needs to be a normalized vector
    Args:
        x: tensor predicted by the model

    Returns:
        normalized predicted cube's orientation quaternion vector
    """
    x = x / tf.norm(x, ord='euclidean')
    return x


def loss_orient(y_true, y_pred):
    """ Create a loss function to deal with translation
    Args:
        y_true: tensor corresponding to the true value of the data
        y_pred: tensor corresponding to the predicted value of the data
            by the model

    Returns:
        loss orientation function for a model
    """

    y_true_orient = y_true[0, :]
    y_pred_orient = y_pred[0, :]

    orientation_loss = 200 * (1 - tf.tensordot(y_pred_orient, y_true_orient, 1))

    orientation_loss = ops.convert_to_tensor_v2(orientation_loss,
                                                dtype=tf.float32)

    loss = orientation_loss
    return loss


def vgg_slam_translation_cube(inputs):
    """ Create the vgg model for the translation of the cube

    Args:
        inputs: shape of the input

    Returns:
        the model not compiled
    """
    model = VGG16(weights='imagenet', input_tensor=inputs)
    # remove the last 3 layers
    model._layers.pop()
    model._layers.pop()
    model._layers.pop()

    x = model.layers[-1].output

    # add two dense layers
    x = layers.Dense(256, activation='relu')(x)
    x = layers.Dense(64, activation='relu')(x)

    # prediction of the coordinates of the cube's center
    predictions_translation = layers.Dense(3, activation="linear",
                                           name='output_trans_cube')(x)

    return predictions_translation


def vgg_slam_orientation_cube(inputs):
    """ Create the vgg model for the orientation of the cube

    Args:
        inputs: shape of the input

    Returns:
        the model not compiled
    """
    model = VGG16(weights='imagenet', input_tensor=inputs)

    # we need to change the name of the layers because it will
    # be in conflicts with the vgg model that we are created for
    # the translation prediction
    for layer in model.layers:
        layer._name = layer.name + str("_orient_cube")

    # remove the last 3 layers
    model._layers.pop()
    model._layers.pop()
    model._layers.pop()
    model._layers.pop()

    x = model.layers[-1].output

    x = layers.Conv2D(filters=512, kernel_size=(3,3), padding="same", activation="relu")(x)
    x = layers.Conv2D(filters=512, kernel_size=(3,3), padding="same", activation="relu")(x)
    x = layers.Conv2D(filters=512, kernel_size=(3,3), padding="same", activation="relu")(x)
    x = layers.MaxPool2D(pool_size=(2,2),strides=(2,2))(x)

    x = layers.Conv2D(filters=1024, kernel_size=(3,3), padding="same", activation="relu")(x)
    x = layers.Conv2D(filters=1024, kernel_size=(3,3), padding="same", activation="relu")(x)
    x = layers.Conv2D(filters=1024, kernel_size=(3,3), padding="same", activation="relu")(x)
    x = layers.MaxPool2D(pool_size=(2,2),strides=(2,2))(x)

    x = layers.Conv2D(filters=1024, kernel_size=(3,3), padding="same", activation="relu")(x)
    x = layers.Conv2D(filters=1024, kernel_size=(3,3), padding="same", activation="relu")(x)
    x = layers.Conv2D(filters=1024, kernel_size=(3,3), padding="same", activation="relu")(x)

    x = layers.Flatten(name='Flattern_orient')(x)

    # add two dense layers
    x = layers.Dense(4096, activation='relu')(x)
    x = layers.Dense(2048, activation='relu')(x)

    # prediction of the coordinates of the cube's center
    predictions_orientation = layers.Dense(2, activation=linear_normalized,
                                           name='output_orient')(x)

    return predictions_orientation


def vgg_slam_translation_sphere(inputs):
    """ Create the vgg model for the translation of the sphere

    Args:
        inputs: shape of the input

    Returns:
        the model not compiled
    """
    model = VGG16(weights='imagenet', input_tensor=inputs)
    for layer in model.layers:
        layer._name = layer.name + str("_trans_sphere")
    # remove the last 3 layers
    model._layers.pop()
    model._layers.pop()
    model._layers.pop()

    x = model.layers[-1].output

    # add two dense layers
    x = layers.Dense(256, activation='relu')(x)
    x = layers.Dense(64, activation='relu')(x)

    # prediction of the coordinates of the cube's center
    predictions_translation = layers.Dense(3, activation="linear",
                                           name='output_trans_sphere')(x)

    return predictions_translation


def vgg_slam_c_s(config):
    """ Create the model

    Args:
        config (dict): estimator config

    Returns:
        the model compiled
    """
    input_shape = (224, 224, 3)
    inputs = Input(shape=input_shape)

    predictions_translation_cube = vgg_slam_translation_cube(inputs)

    predictions_orientation = vgg_slam_orientation_cube(inputs)

    predictions_translation_sphere = vgg_slam_translation_sphere(inputs)

    # this is the model we will train
    model_slam = tf.keras.models.Model(inputs=inputs,
                                       outputs=[predictions_translation_cube,
                                                predictions_orientation,
                                                predictions_translation_sphere])

    adam = tf.keras.optimizers.Adam(lr=config.optimizer.args.lr,
                                    beta_1=config.optimizer.args.beta_1,
                                    beta_2=config.optimizer.args.beta_2,
                                    amsgrad=False)

    model_slam.compile(loss={'output_trans_cube': 'mse',
                             'output_orient': loss_orient,
                             'output_trans_sphere': 'mse'},
                       optimizer=adam)

    return model_slam


class VGGSlamCS(Estimator):

    """VGGSlam model: Neural network based on the VGG16 neural network architecture
    The model is a little bit different from the original one but we still
    import the model as it has already been trained on a huge dataset
    (ImageNet) and even if we change a bit its architecture, the main body
    of it is unchanged and the weights of the final model will not be too
    far from the original one. We call this method "transfer learning".

    This model is used on the SingleCube dataset.

    Attributes:
        config (dict): estimator config
        data_root (str): path towards the data
        split (str): split the data
        version (str): version of the dataset, small and large
        writer: Tensorboard writer object
        checkpointer: Model checkpointer callback to save models
        device: model training on device (cpu|cuda)
    """

    def __init__(self, *, config, writer, checkpointer, device, **kwargs):
        self.config = config
        self.data_root = "/tmp/"  # config.system.data_root # '/tmp/'
        self.uncompress_data_root = os.path.join(self.data_root,
                                                 LOCAL_UNZIP_IMAGES)
        self.backbone = config.backbone
        self.writer = writer
        self.checkpointer = checkpointer
        self.device = device

        # this is for the hyperparameter tuning method
        self.dataset = Dataset.create(
            self.config.train.dataset,
            config=self.config,
            split="train",
            version='single_cube',
            data_root=self.data_root
        )
        self.dataset_df = self.dataset.vgg_slam_data

        # load estimators from file if checkpoint_file exists
        ckpt_file = config.checkpoint_file
        if ckpt_file != const.NULL_STRING:
            # self.load(ckpt_file)  # download from the local
            checkpointer.load(self, ckpt_file)
        else:
            self.model = vgg_slam_c_s(config)

    def _evaluate_one_epoch(
        self,
        X,
        y_orient,
        y_trans_cube,
        y_trans_sphere,
        epoch,
        n_epochs
    ):
        """ Evaluate one epoch

        Args:
            X: input data
            y_orient: target data for the orientation
            y_trans: target data for the translation
            epoch (int): the current epoch number
            n_epochs (int): total epoch number
        """

        logger.info(f" evaluation started")
        metric_trans_cube = EvaluationMetric.create("AverageMeanSquareError")
        metric_trans_sphere = EvaluationMetric.create("AverageMeanSquareError")
        metric_orient = EvaluationMetric.create("AverageQuaternionError")

        for i, row in enumerate(X):
            output_trans_cube, output_orient, \
                output_trans_sphere = self.model.predict(
                    row.reshape(1, 224, 224, -1))

            # for the cube translation
            output_trans_cube = output_trans_cube[0, :].reshape(1, 3)
            target_trans_cube = y_trans_cube[i].reshape(1, 3)

            image_pair_np_translation_cube = (output_trans_cube,
                                              target_trans_cube)
            metric_trans_cube.update(image_pair_np_translation_cube)

            # for the cube orientation
            output_orient = output_orient[0, :].reshape(2)
            target_orient = y_orient[i].reshape(2)

            image_pair_np_orientation = (output_orient, target_orient)
            metric_orient.update(image_pair_np_orientation)

            print("prediction: ", output_orient)
            print("target :", target_orient)

            # for the sphere translation
            output_trans_sphere = output_trans_sphere[0, :].reshape(1, 3)
            target_trans_sphere = y_trans_sphere[i].reshape(1, 3)

            image_pair_np_translation_sphere = (output_trans_sphere,
                                                target_trans_sphere)
            metric_trans_sphere.update(image_pair_np_translation_sphere)

        metric_val_translation_cube = 100 * metric_trans_cube.compute()
        metric_val_translation_sphere = 100 * metric_trans_sphere.compute()
        logger.info(
            f"Epoch[{epoch}/{n_epochs}] evaluation completed.\n"
            f"Average Mean Square Error translation Cube: \
            {metric_val_translation_cube:.3f}\n"
        )

        metric_val_orientation = metric_orient.compute()
        logger.info(
            f"Epoch[{epoch}/{n_epochs}] evaluation completed.\n"
            f"Average Quaternion difference Error: \
            {metric_val_orientation:.3f}\n"
        )

        logger.info(
            f"Epoch[{epoch}/{n_epochs}] evaluation completed.\n"
            f"Average Mean Square Error translation Sphere: \
            {metric_val_translation_sphere:.3f}\n"
        )

        self.writer.add_scalar("Validation/loss_translation_cube",
                               metric_val_translation_cube, epoch)
        self.writer.add_scalar("Validation/loss_orientation",
                               metric_val_orientation, epoch)
        self.writer.add_scalar("Validation/loss_translation_sphere",
                               metric_val_translation_sphere, epoch)

        metric_trans_cube.reset()
        metric_orient.reset()
        metric_trans_sphere.reset()

    def train(self, **kwargs):
        """Abstract method to train estimators
        """
        config = self.config
        writer = self.writer
        dataset = Dataset.create(
            config.train.dataset,
            config=config,
            split="train",
            version='UR3_cube_sphere',
            data_root=self.data_root
        )

        dataset_df = dataset.vgg_slam_data

        X_train, X_val, y_train_orient, y_train_trans_cube, \
            y_val_orient, y_val_trans_cube, y_train_trans_sphere, \
            y_val_trans_sphere = self.data_loader(dataset_df)

        logger.info("Start training estimator: %s", type(self).__name__)

        n_epochs = config.train.epochs
        for epoch in range(1, n_epochs + 1):
            logger.info(f"Training Epoch[{epoch}/{n_epochs}]")

            history = self.model.fit(
                x=X_train,
                y={"output_trans_cube" : y_train_trans_cube,
                   "output_orient" : y_train_orient,
                   "output_trans_sphere" : y_train_trans_sphere},
                batch_size=config.train.batch_size,
                epochs=1,
                validation_data=(X_val,
                                 {"output_trans_cube" : y_val_trans_cube,
                                  "output_orient" : y_val_orient,
                                  "output_trans_sphere" : y_val_trans_sphere})
            )

            train_trans_cube = history.history['output_trans_cube_loss'][-1]
            train_orient = history.history['output_orient_loss'][-1]
            train_trans_sphere = history.history['output_trans_sphere_loss'][-1]

            val_trans_cube = history.history['val_output_trans_cube_loss'][-1]
            val_orient = history.history['val_output_orient_loss'][-1]
            val_trans_sphere = history.history['val_output_trans_sphere_loss'][-1]

            if epoch > 0:
                self._evaluate_one_epoch(X_val, y_val_orient, y_val_trans_cube,
                                         y_val_trans_sphere, epoch, n_epochs)

            logger.info(
                f"Epoch[{epoch}/{n_epochs}] training completed.\n"
                f"Train Loss mse cube: {train_trans_cube:.3f}\n"
                f"Train Loss quaternion: {train_orient:.3f}\n"
                f"Train Loss mse sphere: {train_trans_sphere:.3f}\n"
                f"Validation Loss mse: {val_trans_cube:.3f}\n"
                f"Validation Loss quaternion: {val_orient:.3f}\n"
                f"Validation Loss mse sphere: {val_trans_sphere:.3f}\n"
            )

            writer.add_scalar(
                "Validation/mse_loss cube", val_trans_cube, epoch
            )
            writer.add_scalar(
                "Validation/quaternion_loss", val_orient, epoch
            )
            writer.add_scalar(
                "Validation/mse_loss sphere", val_trans_sphere, epoch
            )

            writer.add_scalar(
                "Train/mse_loss cube", train_trans_cube, epoch
            )
            writer.add_scalar(
                "Train/quaternion_loss", train_orient, epoch
            )
            writer.add_scalar(
                "Train/mse_loss sphere", train_trans_sphere, epoch
            )

            self.save(epoch)

    def evaluate(self, **kwargs):
        """Abstract method to evaluate estimators
        """
        config = self.config
        dataset = Dataset.create(
            config.train.dataset,
            config=config,
            split="train",
            version="UR3_cube_sphere",
            data_root=self.data_root
        )

        val_df = dataset.vgg_slam_data  # meta data of evaluation set
        X_train, X_val, y_train_orient, y_train_trans_cube, y_val_orient, \
            y_val_trans_cube, y_train_trans_sphere, \
            y_val_trans_sphere = self.data_loader(val_df)
        self._evaluate_one_epoch(X_val, y_val_orient, y_val_trans_cube,
                                 y_val_trans_sphere, 1, 1)

    def data_loader(self, df, **kwargs):
        """ Load the data that will feed the model

        Args:
            df: dataframe of the data

        Returns:
            data to feed the model
        """
        X_train = []
        y_train_orient = []
        y_train_trans_cube = []
        X_val = []
        y_val_orient = []
        y_val_trans_cube = []

        y_train_trans_sphere = []
        y_val_trans_sphere = []
        root_dir = self.uncompress_data_root
        files = glob.glob(os.path.join(root_dir, "*.png"))  # your image path

        for myFile in files[:int(0.9 * len(files))]:
            img = image.load_img(myFile, target_size=(224, 224))
            X_train.append(self._image_process(img)[0])
            name_image = myFile.split("/")[-1]
            y_train_orient.append(df[df['screenCaptureName'] == name_image][['q_w', 'q_y']].values[0])
            y_train_trans_cube.append(df[df['screenCaptureName'] == name_image][['x_cube', 'y_cube', 'z_cube']].values[0])
            y_train_trans_sphere.append(df[df['screenCaptureName'] == name_image][['x_sphere', 'y_sphere', 'z_sphere']].values[0])

        for myFile in files[int(0.9 * len(files)):]:
            img = image.load_img(myFile, target_size=(224, 224))
            X_val.append(self._image_process(img)[0])
            name_image = myFile.split("/")[-1]
            y_val_orient.append(df[df['screenCaptureName'] == name_image][['q_w', 'q_y']].values[0])
            y_val_trans_cube.append(df[df['screenCaptureName'] == name_image][['x_cube', 'y_cube', 'z_cube']].values[0])
            y_val_trans_sphere.append(df[df['screenCaptureName'] == name_image][['x_sphere', 'y_sphere', 'z_sphere']].values[0])

        X_train, X_val, y_train_orient, y_train_trans_cube, y_val_orient, \
            y_val_trans_cube, y_train_trans_sphere, y_val_trans_spere = \
            np.array(X_train), np.array(X_val), np.array(y_train_orient), \
            np.array(y_train_trans_cube), np.array(y_val_orient), \
            np.array(y_val_trans_cube), np.array(y_train_trans_sphere), \
            np.array(y_val_trans_sphere)

        return (X_train, X_val, y_train_orient, y_train_trans_cube,
                y_val_orient, y_val_trans_cube, y_train_trans_sphere,
                y_val_trans_spere)

    def save(self, epoch):
        """ Serialize Estimator to path

        Args:
            epoch (int): number of the epoch ran

        Returns:
            saved the model on gcs
        """
        epoch = "ep" + str(epoch)
        file_name = "UR3_cube_sphere_vgg_model7_" + epoch
        checkpoint_file_folder = os.path.join(self.data_root, "models/", epoch)
        # checkpoint_file_folder = "/tmp/test/"  # to save on local
        Path(checkpoint_file_folder).mkdir(parents=True, exist_ok=True)
        checkpoint_file_path = checkpoint_file_folder + "/" + file_name + ".h5"
        self.model.save(checkpoint_file_path)
        copy_folder_to_gcs(GCS_PATH_MODELS, checkpoint_file_folder)

    def load(self, path):
        """ Load Estimator from path

        Args:
            path (str): full path to the serialized estimator
        """
        config = self.config

        self.model = tf.keras.models.load_model(path,
                                                custom_objects={
                                                    'linear_normalized':
                                                    linear_normalized},
                                                compile=False)

        adam = optimizers.Adam(lr=config.optimizer.args.lr,
                               beta_1=config.optimizer.args.beta_1,
                               beta_2=config.optimizer.args.beta_2,
                               amsgrad=False)

        self.model.compile(loss={'output_translation_cube': 'mse',
                                 'output_orientation': loss_orient,
                                 'output_translation_sphere': 'mse'},
                           optimizer=adam)

    def _image_process(self, input_img):
        """ Converts a PIL Image instance to a Numpy array

        Args:
            img: image we want to process

         Returns:
            Preprocessed numpy.array or a tf.Tensor with type float32.
            The images are converted from RGB to BGR, then each color channel
            is zero-centered with respect to the ImageNet dataset, without
            scaling.
        """

        x = image.img_to_array(input_img)
        x = np.expand_dims(x, axis=0)
        return preprocess_input(x)
