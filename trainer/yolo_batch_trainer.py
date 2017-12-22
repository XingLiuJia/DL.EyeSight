# Copyright (c) 2009 IW.
# All rights reserved.
#
# Author: liuguiyang <liuguiyangnwpu@gmail.com>
# Date:   2017/12/20

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse

import os

import pickle as pkl
import matplotlib.pyplot as plt
import numpy as np
import PIL
import tensorflow as tf
from keras import backend as K
from keras.layers import Input, Lambda, Conv2D
from keras.models import load_model, Model
from keras.callbacks import TensorBoard, ModelCheckpoint, EarlyStopping
from keras.callbacks import ModelCheckpoint, LearningRateScheduler, ReduceLROnPlateau

from yolo.feature_base_yolo import (yolo_body, yolo_eval, yolo_head, yolo_loss)
from yolo.draw_boxes import draw_boxes
from yolo.BatchGenerator import process_data, get_detector_mask, BatchGenerator

# Args
argparser = argparse.ArgumentParser(
    description="Batch Train from YOLO framework !")

argparser.add_argument(
    '-d',
    '--train_data_path',
    help="path to numpy data file (.npz) containing np.object array 'boxes' and np.uint8 array 'images'",
    default="/home/ai-i-liuguiyang/repos_ssd/VOC_DATA/VOCdevkit/pascal_voc_07_12_train.pkl")

argparser.add_argument(
    '-v',
    '--val_data_path',
    help="path to numpy data file (.npz) containing np.object array 'boxes' and np.uint8 array 'images'",
    default="/home/ai-i-liuguiyang/repos_ssd/VOC_DATA/VOCdevkit/pascal_voc_07_12_val.pkl")

argparser.add_argument(
    '-a',
    '--anchors_path',
    help='path to anchors file, defaults to yolo_anchors.txt',
    default=os.path.join('model_data', 'yolo_anchors.txt'))

argparser.add_argument(
    '-c',
    '--classes_path',
    help='path to classes file, defaults to pascal_classes.txt',
    default=os.path.join('model_data', 'pascal_classes.txt'))

# Default anchor boxes
YOLO_ANCHORS = np.array(
    ((0.57273, 0.677385), (1.87446, 2.06253), (3.33843, 5.47434),
     (7.88282, 3.52778), (9.77052, 9.16828)))

def _main(args):
    train_data_path = os.path.expanduser(args.train_data_path)
    val_data_path = os.path.expanduser(args.val_data_path)
    classes_path = os.path.expanduser(args.classes_path)
    anchors_path = os.path.expanduser(args.anchors_path)

    class_names = get_classes(classes_path)
    anchors = get_anchors(anchors_path)

    with open(train_data_path, "rb") as train_pkl_file:
        train_data = pkl.load(train_pkl_file)
    with open(val_data_path, "rb") as val_pkl_file:
        val_data = pkl.load(val_pkl_file)
    print("Finsh Load the PKL Source Data !")

    anchors = YOLO_ANCHORS
    
    train_dataset = BatchGenerator(train_data['images'], train_data['boxes'], anchors)
    val_dataset = BatchGenerator(val_data['images'], val_data['boxes'], anchors)

    model_body, model = create_model(anchors, class_names, load_pretrained=False, freeze_body=False)
    train(model, class_names, anchors, train_dataset, val_dataset)
    
    # draw(model_body, class_names, val_boxes, val_image_data, image_set='val', weights_name='trained_stage_3_best.h5', save_all=False)

def get_classes(classes_path):
    '''loads the classes'''
    with open(classes_path) as f:
        class_names = f.readlines()
    class_names = [c.strip() for c in class_names]
    return class_names

def get_anchors(anchors_path):
    '''loads the anchors from a file'''
    if os.path.isfile(anchors_path):
        with open(anchors_path) as f:
            anchors = f.readline()
            anchors = [float(x) for x in anchors.split(',')]
            return np.array(anchors).reshape(-1, 2)
    else:
        Warning("Could not open anchors file, using default.")
        return YOLO_ANCHORS

def create_model(anchors, class_names, load_pretrained=False, freeze_body=False):
    detectors_mask_shape = (13, 13, 5, 1)
    matching_boxes_shape = (13, 13, 5, 5)

    # Create model input layers.
    image_input = Input(shape=(416, 416, 3))
    boxes_input = Input(shape=(None, 5))
    detectors_mask_input = Input(shape=detectors_mask_shape)
    matching_boxes_input = Input(shape=matching_boxes_shape)

    # Create model body.
    yolo_model = yolo_body(image_input, len(anchors), len(class_names))
    topless_yolo = Model(yolo_model.input, yolo_model.layers[-2].output)
    final_layer = Conv2D(len(anchors)*(5+len(class_names)), (1, 1), activation='linear')(topless_yolo.output)

    model_body = Model(image_input, final_layer)

    model_loss = Lambda(
        yolo_loss,
        output_shape = (1, ),
        name = 'yolo_loss',
        arguments = {
            'anchors': anchors,
            'num_classes': len(class_names)
        })([
            model_body.output,
            boxes_input,
            detectors_mask_input,
            matching_boxes_input
            ])

    model = Model([model_body.input, boxes_input, detectors_mask_input, matching_boxes_input], model_loss)

    return model_body, model

def train(model, class_names, anchors, train_dataset, val_dataset):
    model.compile(
        optimizer = 'adam',
        loss = {
            'yolo_loss': lambda y_true, y_pred: y_pred
        })

    def lr_schedule(epoch):
        if epoch <= 500:
            return 0.001
        elif epoch <= 800:
            return 0.0005
        else:
            return 0.00001

    epochs = 1000
    n_train_samples = train_dataset.get_n_samples()
    n_val_samples   = val_dataset.get_n_samples()

    batch_size = 10
    train_generator = train_dataset.generate(batch_size=batch_size, shuffle=True, train=True)
    val_generator = train_dataset.generate(batch_size=batch_size, shuffle=False, train=True)

    history = model.fit_generator(generator = train_generator,
                                  steps_per_epoch = np.ceil(n_train_samples/batch_size),
                                  epochs = epochs,
                                  callbacks = [ModelCheckpoint('weights/yolo_weights_epoch-{epoch:02d}_loss-{loss:.4f}_val_loss-{val_loss:.4f}.h5',
                                                               monitor='val_loss',
                                                               verbose=1,
                                                               save_best_only=True,
                                                               save_weights_only=True,
                                                               mode='auto',
                                                               period=1),
                                               LearningRateScheduler(lr_schedule),
                                               EarlyStopping(monitor='val_loss',
                                                             min_delta=0.00001,
                                                             patience=20)],
                                  validation_data = val_generator,
                                  validation_steps = np.ceil(n_val_samples/batch_size))

    model.save_weights('weights/yolo_weights.h5')
    # model.save_weights('trained_stage_1.h5')

    # model_body, model = create_model(anchors, class_names, load_pretrained=False, freeze_body=False)
    # model.load_weights('trained_stage_1.h5')
    # model.compile(
    #     optimizer='adam', loss={
    #         'yolo_loss': lambda y_true, y_pred: y_pred
    #     })  # This is a hack to use the custom loss function in the last layer.

    # model.fit([image_data, boxes, detectors_mask, matching_true_boxes],
    #           np.zeros(len(image_data)),
    #           validation_split=0.1,
    #           batch_size=8,
    #           epochs=30,
    #           callbacks=[logging])
    # model.save_weights('trained_stage_2.h5')

    # model.fit([image_data, boxes, detectors_mask, matching_true_boxes],
    #           np.zeros(len(image_data)),
    #           validation_split=0.1,
    #           batch_size=8,
    #           epochs=30,
    #           callbacks=[logging, checkpoint, early_stopping])
    # model.save_weights('trained_stage_3.h5')

def draw(model_body, class_names, anchors, image_data, image_set='val',
            weights_name='trained_stage_3_best.h5', out_path="output_images", save_all=True):
    '''
    Draw bounding boxes on image data
    '''
    if image_set == 'train':
        image_data = np.array([np.expand_dims(image, axis=0)
            for image in image_data[:int(len(image_data)*.9)]])
    elif image_set == 'val':
        image_data = np.array([np.expand_dims(image, axis=0)
            for image in image_data[int(len(image_data)*.9):]])
    elif image_set == 'all':
        image_data = np.array([np.expand_dims(image, axis=0)
            for image in image_data])
    else:
        ValueError("draw argument image_set must be 'train', 'val', or 'all'")
    
    model_body.load_weights(weights_name)

    # Create output variables for prediction.
    yolo_outputs = yolo_head(model_body.output, anchors, len(class_names))
    input_image_shape = K.placeholder(shape=(2, ))
    boxes, scores, classes = yolo_eval(
        yolo_outputs, input_image_shape, score_threshold=0.07, iou_threshold=0)

    # Run prediction on overfit image.
    sess = K.get_session()  # TODO: Remove dependence on Tensorflow session.

    if  not os.path.exists(out_path):
        os.makedirs(out_path)
    for i in range(len(image_data)):
        out_boxes, out_scores, out_classes = sess.run(
            [boxes, scores, classes],
            feed_dict={
                model_body.input: image_data[i],
                input_image_shape: [image_data.shape[2], image_data.shape[3]],
                K.learning_phase(): 0
            })
        print('Found {} boxes for image.'.format(len(out_boxes)))
        print(out_boxes)

        # Plot image with predicted boxes.
        image_with_boxes = draw_boxes(image_data[i][0], out_boxes, out_classes,
                                    class_names, out_scores)
        # Save the image:
        if save_all or (len(out_boxes) > 0):
            image = PIL.Image.fromarray(image_with_boxes)
            image.save(os.path.join(out_path,str(i)+'.png'))

        # To display (pauses the program):
        # plt.imshow(image_with_boxes, interpolation='nearest')
        # plt.show()


if __name__ == '__main__':
    args = argparser.parse_args()
    _main(args)