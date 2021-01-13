import os

import tensorflow as tf
from absl import app, flags, logging

from retinanet.cfg import Config
from retinanet.dataloader import PreprocessingPipeline
from retinanet.model import model_builder, prepare_model_for_export
from retinanet.trainer import Trainer

tf.get_logger().propagate = False
tf.config.set_soft_device_placement(True)

flags.DEFINE_string('config_path',
                    default=None,
                    help='Path to the config file')

flags.DEFINE_string('export_dir',
                    default='export',
                    help='Path to store the model artefacts')

flags.DEFINE_boolean('export_saved_model',
                     default=False,
                     help='Export weights as a `saved_model`')

flags.DEFINE_boolean('export_h5',
                     default=False,
                     help='Export weights as an h5 file (can be used for fine tuning)')  # noqa: E501

flags.DEFINE_string('overide_model_dir',
                    default='null',
                    help='Use local filesystem to load checkpoint')

flags.DEFINE_boolean('debug', default=False, help='Print debugging info')

FLAGS = flags.FLAGS


def main(_):
    logging.set_verbosity(logging.DEBUG if FLAGS.debug else logging.INFO)

    params = Config(FLAGS.config_path).params

    if FLAGS.log_dir and (not os.path.exists(FLAGS.log_dir)):
        os.makedirs(FLAGS.log_dir, exist_ok=True)

    logging.get_absl_handler().use_absl_log_file(
        'export_' + params.experiment.name)

    if not FLAGS.overide_model_dir == 'null':
        params.experiment.model_dir = FLAGS.overide_model_dir
        logging.warning('Using local path {} as `model_dir`'.format(
            params.experiment.model_dir))

    run_mode = 'export'

    # skip loading pretrained backbone weights
    params.architecture.backbone.checkpoint = ''

    train_input_fn = None
    val_input_fn = None
    model_fn = model_builder(params)

    trainer = Trainer(
        params=params,
        strategy=tf.distribute.OneDeviceStrategy(device='/cpu:0'),
        run_mode=run_mode,
        model_fn=model_fn,
        train_input_fn=train_input_fn,
        val_input_fn=val_input_fn
    )

    trainer.restore_status.assert_consumed()

    if FLAGS.export_h5:
        export_dir = os.path.join(FLAGS.export_dir, trainer.name)

        if not os.path.exists(export_dir):
            os.makedirs(export_dir, exist_ok=True)

        latest_checkpoint = os.path.basename(
            tf.train.latest_checkpoint(trainer.model_dir))

        export_filename = os.path.join(export_dir, latest_checkpoint + '.h5')

        logging.info(
            'Exporting `weights in h5 format` to {}'.format(export_filename))

        trainer.model.save_weights(export_filename)

    if FLAGS.export_saved_model:
        logging.info('Exporting `saved_model` to {}'.format(FLAGS.export_dir))

        preprocessing_pipeling = PreprocessingPipeline(
            params.input.input_shape, params.dataloader_params)

        @tf.function(input_signature=[{
            'image': tf.TensorSpec(shape=[None, None, 3],
                                   name='image',
                                   dtype=tf.float32),
            'image_id': tf.TensorSpec(shape=[],
                                      name='image_id',
                                      dtype=tf.int32)
        }

        ])
        def serving_fn(sample):
            image_dict = preprocessing_pipeling.preprocess_val_sample(sample)

            image = tf.expand_dims(image_dict['image'], axis=0)
            resize_scale = tf.tile(tf.expand_dims(
                image_dict['resize_scale'], axis=0), multiples=[1, 2])

            detections = inference_model.call(image, training=False)

            return {
                'image_id': sample['image_id'],
                'boxes': detections.nmsed_boxes[0] / resize_scale,
                'scores': detections.nmsed_scores[0],
                'classes': detections.nmsed_classes[0],
                'valid_detections': detections.valid_detections[0]
            }

        inference_model = prepare_model_for_export(trainer)

        tf.saved_model.save(
            inference_model,
            os.path.join(FLAGS.export_dir, params.experiment.name),
            signatures={'serving_default': serving_fn.get_concrete_function()})


if __name__ == '__main__':
    app.run(main)
