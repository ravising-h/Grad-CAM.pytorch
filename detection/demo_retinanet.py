# -*- coding: utf-8 -*-
"""
 @File    : demo_retinanet.py
 @Time    : 2020/5/16 下午9:59
 @Author  : yizuotian
 @Description    :
"""

import argparse
import multiprocessing as mp
import os

from tqdm import tqdm
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1 import ImageGrid
import cv2
import detectron2.data.transforms as T
import numpy as np
import torch
from detectron2.checkpoint import DetectionCheckpointer
from detectron2.config import get_cfg
from detectron2.data import MetadataCatalog
from detectron2.data.detection_utils import read_image
from detectron2.modeling import build_model
from detectron2.utils.logger import setup_logger
from skimage import io

from grad_cam_retinanet import GradCAM, GradCamPlusPlus

# constants
WINDOW_NAME = "COCO detections"


def setup_cfg(args):
    # load config from file and command-line arguments
    cfg = get_cfg()
    cfg.merge_from_file(args.config_file)
    cfg.merge_from_list(args.opts)
    # Set score_threshold for builtin models
    cfg.MODEL.RETINANET.SCORE_THRESH_TEST = 0.25
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = args.confidence_threshold
    cfg.MODEL.PANOPTIC_FPN.COMBINE.INSTANCES_CONFIDENCE_THRESH = args.confidence_threshold
    cfg.freeze()
    return cfg


def norm_image(image):
    """
    标准化图像
    :param image: [H,W,C]
    :return:
    """
    image = image.copy()
    image -= np.max(np.min(image), 0)
    image /= np.max(image)
    image *= 255.
    return np.uint8(image)


def gen_cam(image, mask):
    """
    生成CAM图
    :param image: [H,W,C],原始图像
    :param mask: [H,W],范围0~1
    :return: tuple(cam,heatmap)
    """
    # mask转为heatmap
    heatmap = cv2.applyColorMap(np.uint8(255 * mask), cv2.COLORMAP_JET)
    heatmap = np.float32(heatmap) / 255
    heatmap = heatmap[..., ::-1]  # gbr to rgb

    # 合并heatmap到原始图像
    cam = heatmap + np.float32(image)
    return norm_image(cam), heatmap


def save_image(image_dicts, input_image_name, layer_name, network='retinanet', output_dir='./results'):
    prefix = os.path.splitext(input_image_name)[0]
    for key, image in image_dicts.items():
        if key == 'predict_box':
            io.imsave(os.path.join(output_dir,
                                   '{}-{}-{}.jpg'.format(prefix, network, key)),
                      image)
        else:
            io.imsave(os.path.join(output_dir,
                                   '{}-{}-{}-{}.jpg'.format(prefix, network, layer_name, key)),
                      (255 * image).astype(np.uint8) )


def get_parser():
    parser = argparse.ArgumentParser(description="Detectron2 demo for builtin models")
    parser.add_argument(
        "--config-file",
        default="configs/quick_schedules/mask_rcnn_R_50_FPN_inference_acc_test.yaml",
        metavar="FILE",
        help="path to config file",
    )
    parser.add_argument("--input", help="A list of space separated input images")
    parser.add_argument(
        "--output",
        help="A file or directory to save output visualizations. "
             "If not given, will show output in an OpenCV window.",
    )

    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.2,
        help="Minimum score for instance predictions to be shown",
    )
    parser.add_argument(
        "--opts",
        help="Modify config options using the command-line 'KEY VALUE' pairs",
        default=[],
        nargs=argparse.REMAINDER,
    )
    parser.add_argument('--layer-name', type=str, default='head.cls_subnet.2',
                        help='使用哪层特征去生成CAM')
    return parser


def main(args):
    from detectron2.data.datasets import register_coco_instances
    register_coco_instances("Dent_Detection_train", {}, "/content/drive/MyDrive/Damage/Dataset/coco_15k_balanced/annotations/instance_train2017.json", "/content/drive/MyDrive/Damage/Dataset/coco_15k_balanced/JPEGImages")
    register_coco_instances("Dent_Detection_test", {}, "/content/drive/MyDrive/Damage/Dataset/coco_15k_balanced/annotations/instance_val2017.json", "/content/drive/MyDrive/Damage/Dataset/coco_15k_balanced/JPEGImages")
    MetadataCatalog.get("Dent_Detection_test").thing_classes = ['Dent']
    setup_logger(name="fvcore")
    logger = setup_logger()
    logger.info("Arguments: " + str(args))

    cfg = setup_cfg(args)
    print(cfg)
    
    model = build_model(cfg)

    checkpointer = DetectionCheckpointer(model)
    checkpointer.load(cfg.MODEL.WEIGHTS)


    path = os.path.expanduser(args.input)
    original_image = read_image(path, format="BGR")
    height, width = original_image.shape[:2]
    transform_gen = T.ResizeShortestEdge(
        [cfg.INPUT.MIN_SIZE_TEST, cfg.INPUT.MIN_SIZE_TEST], cfg.INPUT.MAX_SIZE_TEST
    )
    image = transform_gen.get_transform(original_image).apply_image(original_image)
    image = torch.as_tensor(image.astype("float32").transpose(2, 0, 1)).requires_grad_(True)

    inputs = {"image": image, "height": height, "width": width}

    # Grad-CAM
    img_grid = [0]*18
    for ly in tqdm(range(8),  desc ="LAYER BY LAYER"):
        layer_name = f'head.cls_subnet.{ly}'
        grad_cam = GradCAM(model, layer_name)
        mask, box, class_id = grad_cam(inputs)  # cam mask
        grad_cam.remove_handlers()

        #
        image_dict = {}
        img = original_image[..., ::-1]
        x1, y1, x2, y2 = box
        image_dict['predict_box'] = img[y1:y2, x1:x2]
        img_grid[0], img_grid[9] = image_dict['predict_box'] , image_dict['predict_box'] 
        image_cam, image_dict['heatmap'] = gen_cam(img[y1:y2, x1:x2], mask[y1:y2, x1:x2])
        img_grid[ly+1] = (image_dict['heatmap']*255).astype(np.uint8)
        
        # Grad-CAM++
        grad_cam_plus_plus = GradCamPlusPlus(model, layer_name)
        mask_plus_plus = grad_cam_plus_plus(inputs)  # cam mask

        _, image_dict['heatmap++'] = gen_cam(img[y1:y2, x1:x2], mask_plus_plus[y1:y2, x1:x2])
        img_grid[ly+9] = (image_dict['heatmap++']*255).astype(np.uint8)
        grad_cam_plus_plus.remove_handlers()

        # 获取类别名称
        meta = MetadataCatalog.get(
            cfg.DATASETS.TEST[0] if len(cfg.DATASETS.TEST) else "__unused"
        )
        label = meta.thing_classes[class_id]

    print("label:{}".format(label))
        
    fig = plt.figure(figsize=(20., 6.))
    grid = ImageGrid(fig, 111,  # similar to subplot(111)
                        nrows_ncols=(2, 9),  # creates 2x2 grid of axes
                        axes_pad=0.1,  # pad between axes in inch.
                        )

    for ax, im_ in zip(grid, img_grid):
        # Iterating over the grid returns the Axes.
        ax.imshow(cv2.addWeighted(im_, 0.6, img_grid[0], 0.4, 0.2))
        
    plt.savefig(os.path.join(output_dir, f'{os.path.basename(path)}-grid.jpg'))
    # save_image(image_dict, os.path.basename(path), args.layer_name)


if __name__ == "__main__":
    """
    Usage:export KMP_DUPLICATE_LIB_OK=TRUE
    python detection/demo_retinanet.py --config-file detection/retinanet_R_50_FPN_3x.yaml \
      --input ./examples/pic1.jpg \
      --layer-name head.cls_subnet.7 \
      --opts MODEL.WEIGHTS /Users/yizuotian/pretrained_model/model_final_4cafe0.pkl MODEL.DEVICE cpu
    """
    mp.set_start_method("spawn", force=True)
    arguments = get_parser().parse_args()
    main(arguments)
