import os
import numpy as np
import tensorflow as tf
import random
import cv2 as cv
import sys
from network.logger import setup_logger
sys.path.append(os.path.dirname(__file__) + os.sep + '../')
import config as FLAGS

root_dir = os.path.dirname(__file__).split('MTCNN')[0]
project_dir = os.path.dirname(__file__).split('MTCNN')[1]

def train(net_factory, model_prefix, end_epoch, base_dir, display, base_lr):
    '''训练模型'''
    size = int(base_dir.split('/')[-1])
    if size == 12:
        net = 'pnet'
        radio_cls_loss, radio_bbox_loss, radio_landmark_loss = 1.0, 0.5, 0.5
    elif size == 24:
        net = 'rnet'
        radio_cls_loss, radio_bbox_loss, radio_landmark_loss = 1.0, 0.5, 0.5
    elif size == 48:
        net = 'onet'
        radio_cls_loss, radio_bbox_loss, radio_landmark_loss = 1.0, 0.5, 1.0

    log = setup_logger(root_dir + 'MTCNN' + '/checkpoint/%s/logs' % net)
    if net == 'pnet':
        # 计算一共多少组数据
        label_file=os.path.join(base_dir,'pnet_landmark.txt')
        f = open(label_file, 'r')
        # total number of PNet train dataset
        num = len(f.readlines())
        dataset_dir = os.path.join(base_dir,'tfrecord/pnet.tfrecord_shuffle')
        # 从tfrecord读取数据
        image_batch, label_batch, bbox_batch, landmark_batch = read_single_tfrecord(dataset_dir, FLAGS.batch_size, net)
    else:
        # 计算一共多少组数据
        label_file1=os.path.join(base_dir, 'pos_%d.txt' % size)
        f1 = open(label_file1, 'r')
        label_file2=os.path.join(base_dir, 'part_%d.txt' % size)
        f2 = open(label_file2, 'r')
        label_file3=os.path.join(base_dir, 'neg_%d.txt' % size)
        f3 = open(label_file3, 'r')
        label_file4=os.path.join(base_dir, 'lfw_wider_landmark.txt')
        f4 = open(label_file4, 'r')
        num = len(f1.readlines()) + len(f2.readlines()) + len(f3.readlines()) + len(f4.readlines())
    
        pos_dir = os.path.join(base_dir, 'tfrecord/pos_%d.tfrecord_shuffle' % size)
        part_dir = os.path.join(base_dir, 'tfrecord/part_%d.tfrecord_shuffle' % size)
        neg_dir = os.path.join(base_dir, 'tfrecord/neg_%d.tfrecord_shuffle' % size)
        landmark_dir = os.path.join(base_dir, 'tfrecord/landmark_%d.tfrecord_shuffle' % size)
        dataset_dirs = [pos_dir, part_dir, neg_dir, landmark_dir]

        # 各数据占比, 目的是使每一个batch的数据占比都相同
        pos_radio, part_radio, landmark_radio, neg_radio = 1.0/6, 1.0/6, 1.0/6, 3.0/6
        pos_batch_size = int(np.ceil(FLAGS.batch_size * pos_radio))
        assert pos_batch_size != 0, "Batch Size 有误 "
        part_batch_size = int(np.ceil(FLAGS.batch_size * part_radio))
        assert part_batch_size != 0, "BBatch Size 有误 "
        neg_batch_size = int(np.ceil(FLAGS.batch_size * neg_radio))
        assert neg_batch_size != 0,"Batch Size 有误 "
        landmark_batch_size = int(np.ceil(FLAGS.batch_size * landmark_radio))
        assert landmark_batch_size != 0, "Batch Size 有误 "
        batch_sizes = [pos_batch_size, part_batch_size, neg_batch_size, landmark_batch_size]
        image_batch, label_batch, bbox_batch, landmark_batch = read_multi_tfrecords(dataset_dirs, batch_sizes, net)
        
    input_image = tf.placeholder(tf.float32, shape=[FLAGS.batch_size, size, size, 3], name='input_image')
    label = tf.placeholder(tf.float32, shape=[FLAGS.batch_size], name='label')
    bbox_target = tf.placeholder(tf.float32, shape=[FLAGS.batch_size, 4], name='bbox_target')
    landmark_target = tf.placeholder(tf.float32, shape=[FLAGS.batch_size, 10], name='landmark_target')
    # 图像色相变换
    input_image = image_color_distort(input_image)
    cls_loss_op, bbox_loss_op, landmark_loss_op, L2_loss_op, accuracy_op = net_factory(input_image, label, bbox_target, landmark_target, training=True)
    total_loss_op = radio_cls_loss * cls_loss_op + radio_bbox_loss * bbox_loss_op + radio_landmark_loss * landmark_loss_op + L2_loss_op
    train_op, lr_op = optimize(base_lr, total_loss_op, num)
    
    tf.summary.scalar("cls_loss", cls_loss_op)  # cls_loss
    tf.summary.scalar("bbox_loss", bbox_loss_op)  # bbox_loss
    tf.summary.scalar("landmark_loss", landmark_loss_op)  # landmark_loss
    tf.summary.scalar("cls_accuracy", accuracy_op)  # cls_acc
    tf.summary.scalar("total_loss", total_loss_op)  # cls_loss, bbox loss, landmark loss and L2 loss add together
    summary_op = tf.summary.merge_all()
    logs_dir = root_dir + 'MTCNN' + '/checkpoint/%s' % (net)
    if not os.path.exists(logs_dir):
        os.mkdir(logs_dir)
    # 模型训练
    saver = tf.train.Saver(max_to_keep=5)
    init = tf.global_variables_initializer()
    sess = tf.Session()
    sess.run(init)
    if FLAGS.restore:
        model_file = tf.train.latest_checkpoint(root_dir + 'MTCNN' + '/checkpoint/onet')
        saver.restore(sess, model_file)
    # 模型的graph
    writer = tf.summary.FileWriter(logs_dir, sess.graph) 
    
    coord = tf.train.Coordinator() 
    threads = tf.train.start_queue_runners(sess=sess, coord=coord)

    i = 0
    MAX_STEP = int(num / FLAGS.batch_size + 1) * end_epoch
    epoch = 0
    sess.graph.finalize()
    try:
        for step in range(MAX_STEP):
            i = i + 1
            if coord.should_stop():
                break
            image_batch_array, label_batch_array, bbox_batch_array, landmark_batch_array = sess.run([image_batch, label_batch, bbox_batch, landmark_batch])
            # 随机翻转图像
            image_batch_array, landmark_batch_array = random_flip_images(image_batch_array, label_batch_array, landmark_batch_array)
            _, _, summary = sess.run([train_op, lr_op, summary_op], feed_dict={input_image: image_batch_array,
                                                                               label: label_batch_array,
                                                                               bbox_target: bbox_batch_array,
                                                                               landmark_target: landmark_batch_array})
            # 展示训练过程
            if (step+1) % display == 0:
                cls_loss, bbox_loss, landmark_loss, L2_loss, lr, acc = sess.run([cls_loss_op, bbox_loss_op, landmark_loss_op, L2_loss_op, lr_op, accuracy_op],
                                                                                feed_dict={input_image: image_batch_array,
                                                                                label: label_batch_array,
                                                                                bbox_target: bbox_batch_array,
                                                                                landmark_target: landmark_batch_array})
                total_loss = radio_cls_loss * cls_loss + radio_bbox_loss * bbox_loss + radio_landmark_loss * landmark_loss + L2_loss
                log.info('epoch:%d/%d'%(epoch+1,end_epoch))
                log.info("Step: %d/%d, accuracy: %3f, cls loss: %4f, bbox loss: %4f,Landmark loss :%4f,L2 loss: %4f, Total Loss: %4f ,lr:%f " %
                      (step+1, MAX_STEP, acc, cls_loss, bbox_loss, landmark_loss, L2_loss, total_loss, lr))

            # 每一次epoch保留一次模型
            if i * FLAGS.batch_size > num:
                epoch = epoch + 1
                i = 0
                if FLAGS.restore:
                    model_prefix += '_refine'
                saver.save(sess, model_prefix, global_step=epoch)
            writer.add_summary(summary, global_step=step)
    except tf.errors.OutOfRangeError:
        log.info("完成！！！")
    finally:
        coord.request_stop()
        writer.close()
    coord.join(threads)
    sess.close()


def optimize(base_lr, loss, data_num):
    '''参数优化'''
    lr_factor = 0.1
    global_step = tf.Variable(0, trainable=False)
    boundaries = [int(epoch * data_num / FLAGS.batch_size) for epoch in FLAGS.lr_epoch]
    lr_values = [base_lr * (lr_factor ** x) for x in range(0, len(FLAGS.lr_epoch) + 1)]  # [0.01,0.001,0.0001,0.00001]
    lr_op = tf.train.piecewise_constant(global_step, boundaries, lr_values)
    optimizer = tf.train.MomentumOptimizer(lr_op, 0.9)
    train_op = optimizer.minimize(loss, global_step)
    return train_op, lr_op


def read_single_tfrecord(tfrecord_file, batch_size, net):
    '''读取tfrecord数据'''
    filename_queue = tf.train.string_input_producer([tfrecord_file], shuffle=True)
    reader = tf.TFRecordReader()
    _, serialized_example = reader.read(filename_queue)
    image_features = tf.parse_single_example(serialized_example,
                        features={
                        'image/encoded': tf.FixedLenFeature([], tf.string),
                        'image/label': tf.FixedLenFeature([], tf.int64),
                        'image/roi': tf.FixedLenFeature([4], tf.float32),
                        'image/landmark': tf.FixedLenFeature([10],tf.float32)
                    }
                )
    if net == 'pnet':
        image_size = 12
    elif net == 'rnet':
        image_size = 24
    elif net == 'onet':
        image_size = 48
    image = tf.decode_raw(image_features['image/encoded'], tf.uint8)
    image = tf.reshape(image, [image_size, image_size, 3])
    # 将值规划在[-1,1]内
    image = (tf.cast(image, tf.float32)-127.5)/128
    
    label = tf.cast(image_features['image/label'], tf.float32)
    roi = tf.cast(image_features['image/roi'], tf.float32)
    landmark = tf.cast(image_features['image/landmark'], tf.float32)
    image, label, roi, landmark = tf.train.batch([image, label, roi, landmark], batch_size=batch_size, num_threads=8, capacity=batch_size)
    label = tf.reshape(label, [batch_size])
    roi = tf.reshape(roi, [batch_size, 4])
    landmark = tf.reshape(landmark,[batch_size,10])
    return image, label, roi, landmark


def read_multi_tfrecords(tfrecord_files, batch_sizes, net):
    '''读取多个tfrecord文件放一起'''
    pos_dir, part_dir, neg_dir, landmark_dir = tfrecord_files
    pos_batch_size, part_batch_size, neg_batch_size, landmark_batch_size = batch_sizes
    pos_image, pos_label, pos_roi, pos_landmark = read_single_tfrecord(pos_dir, pos_batch_size, net)
    part_image, part_label, part_roi, part_landmark = read_single_tfrecord(part_dir, part_batch_size, net)
    neg_image, neg_label, neg_roi, neg_landmark = read_single_tfrecord(neg_dir, neg_batch_size, net)
    landmark_image, landmark_label, landmark_roi, landmark_landmark = read_single_tfrecord(landmark_dir, landmark_batch_size, net)

    images = tf.concat([pos_image, part_image, neg_image, landmark_image], 0, name="concat/image")
    labels = tf.concat([pos_label, part_label, neg_label, landmark_label], 0, name="concat/label")
    assert isinstance(labels, object)
    rois = tf.concat([pos_roi, part_roi, neg_roi, landmark_roi], 0, name="concat/roi")
    landmarks = tf.concat([pos_landmark, part_landmark, neg_landmark, landmark_landmark], 0, name="concat/landmark")
    return images, labels, rois, landmarks
    

def image_color_distort(inputs):
    inputs = tf.image.random_contrast(inputs, lower=0.5, upper=1.5)
    inputs = tf.image.random_brightness(inputs, max_delta=0.2)
    inputs = tf.image.random_hue(inputs, max_delta=0.2)
    inputs = tf.image.random_saturation(inputs, lower=0.5, upper=1.5)
    return inputs


# don't flip for pos iamge_batch?????
def random_flip_images(image_batch, label_batch, landmark_batch):
    '''随机翻转图像'''
    if random.choice([0, 1]) > 0:
        fliplandmarkindexes = np.where(label_batch == -2)[0]
        flipposindexes = np.where(label_batch == 1)[0]
        
        flipindexes = np.concatenate((fliplandmarkindexes, flipposindexes))
          
        for i in flipindexes:
            cv.flip(image_batch[i], 1, image_batch[i])  # 1: horizontal flip  0: vertical -1: both axes.

        for i in fliplandmarkindexes:
            landmark_ = landmark_batch[i].reshape((-1, 2))
            landmark_ = np.asarray([(1-x, y) for (x, y) in landmark_])
            landmark_[[0, 1]] = landmark_[[1, 0]]
            landmark_[[3, 4]] = landmark_[[4, 3]]       
            landmark_batch[i] = landmark_.ravel()
        
    return image_batch, landmark_batch

