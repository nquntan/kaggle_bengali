import os

import albumentations as A
import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image
import torch
from albumentations.augmentations import functional as F
from albumentations.core.transforms_interface import DualTransform
from skimage.transform import AffineTransform, warp

from transformars.augmix import RandomAugMix
from transformars.grid_mask import GridMask
from transformars.autoaugment import ImageNetPolicy

HEIGHT = 137
WIDTH = 236

policy = ImageNetPolicy()

def affine_image(img):

    h, w, ch = img.shape

    # --- scale ---
    min_scale = 0.8
    max_scale = 1.2
    sx = np.random.uniform(min_scale, max_scale)
    sy = np.random.uniform(min_scale, max_scale)

    # --- rotation ---
    max_rot_angle = 7
    rot_angle = np.random.uniform(-max_rot_angle, max_rot_angle) * np.pi / 180.

    # --- shear ---
    max_shear_angle = 10
    shear_angle = np.random.uniform(-max_shear_angle, max_shear_angle) * np.pi / 180.

    # --- translation ---
    max_translation = 4
    tx = np.random.randint(-max_translation, max_translation)
    ty = np.random.randint(-max_translation, max_translation)

    tform = AffineTransform(scale=(sx, sy), rotation=rot_angle, shear=shear_angle,
                            translation=(tx, ty))
    transformed_image = warp(img, tform)
    return transformed_image


def add_gaussian_noise(x, sigma):
    x += np.random.randn(*x.shape) * sigma
    x = np.clip(x, 0., 1.)
    return x


def _evaluate_ratio(ratio):
    if ratio <= 0.:
        return False
    return np.random.uniform() < ratio


def apply_aug(aug, image):
    return aug(image=image)['image']


class Transform:
    def __init__(self, affine=False, crop=False, size=224,
                 autoaugment=False, normalize=True, train=True, threshold=40.,
                 sigma=-1., blur_ratio=0., noise_ratio=0., cutout_ratio=0.,
                 grid_distortion_ratio=0., elastic_distortion_ratio=0., random_brightness_ratio=0.,
                 piece_affine_ratio=0., ssr_ratio=0., grid_mask_ratio=0., augmix_ratio=0.):
        self.affine = affine
        self.crop = crop
        self.size = size
        self.normalize = normalize
        self.train = train
        self.threshold = threshold / 255.
        self.sigma = sigma / 255.
        self.autoaugment = autoaugment

        self.blur_ratio = blur_ratio
        self.noise_ratio = noise_ratio
        self.cutout_ratio = cutout_ratio
        self.grid_distortion_ratio = grid_distortion_ratio
        self.elastic_distortion_ratio = elastic_distortion_ratio
        self.random_brightness_ratio = random_brightness_ratio
        self.piece_affine_ratio = piece_affine_ratio
        self.ssr_ratio = ssr_ratio
        self.grid_mask_ratio = grid_mask_ratio
        self.augmix_ratio = augmix_ratio

    def __call__(self, example):
        if self.train:
            x = example
        else:
            x = example
        # --- Augmentation ---
        if self.affine:
            x = affine_image(x)

        # --- Train/Test common preprocessing ---
        if self.crop:
            x = crop_char_image(x, threshold=self.threshold)
        if self.size is not None:
            x = apply_aug(A.Resize(128, 128, always_apply=True), x)
        if self.sigma > 0.:
            x = add_gaussian_noise(x, sigma=self.sigma)

        if self.autoaugment:
            x = policy(Image.fromarray(x).convert("RGB"))
            x = np.array(x)

        # albumentations...
        x = x.astype(np.float32)

        if _evaluate_ratio(self.augmix_ratio):
            r = np.random.uniform()
            if r < 0.30:
                x = apply_aug(RandomAugMix(severity=1, width=1, p=1.), x)
            elif r < 0.5:
                x = apply_aug(RandomAugMix(severity=3, width=7, alpha=5, p=1.), x)
            elif r < 0.75:
                x = apply_aug(RandomAugMix(severity=1, width=1, p=1.), x)
            else:
                x = apply_aug(RandomAugMix(severity=3, width=7, alpha=5, p=1.), x)

        if _evaluate_ratio(self.grid_mask_ratio):
            r = np.random.uniform()
            if r < 0.25:
                x = apply_aug(GridMask(num_grid=3, p=1), x)
            elif r < 0.5:
                x = apply_aug(GridMask(num_grid=(3, 7), p=1), x)
            elif r < 0.75:
                x = apply_aug(GridMask(num_grid=3, rotate=15, p=1), x)
            else:
                x = apply_aug(GridMask(num_grid=(3, 7), mode=1, p=1), x)

        # 1. blur
        if _evaluate_ratio(self.blur_ratio):
            r = np.random.uniform()
            if r < 0.25:
                x = apply_aug(A.Blur(p=1.0), x)
            elif r < 0.5:
                x = apply_aug(A.MedianBlur(blur_limit=5, p=1.0), x)
            elif r < 0.75:
                x = apply_aug(A.GaussianBlur(p=1.0), x)
            else:
                x = apply_aug(A.MotionBlur(p=1.0), x)

        if _evaluate_ratio(self.noise_ratio):
            r = np.random.uniform()
            if r < 0.50:
                x = apply_aug(A.GaussNoise(var_limit=5. / 255., p=1.0), x)
            else:
                x = apply_aug(A.MultiplicativeNoise(p=1.0), x)

        if _evaluate_ratio(self.cutout_ratio):
            x = apply_aug(A.CoarseDropout(max_holes=8, max_height=8, max_width=8, p=1.0), x)

        if _evaluate_ratio(self.grid_distortion_ratio):
            x = apply_aug(A.GridDistortion(p=1.0), x)

        if _evaluate_ratio(self.elastic_distortion_ratio):
            x = apply_aug(A.ElasticTransform(
                sigma=50, alpha=1, alpha_affine=10, p=1.0), x)

        if _evaluate_ratio(self.random_brightness_ratio):
            x = apply_aug(A.RandomBrightnessContrast(p=1.0), x)

        if _evaluate_ratio(self.piece_affine_ratio):
            x = apply_aug(A.IAAPiecewiseAffine(p=1.0), x)

        if _evaluate_ratio(self.ssr_ratio):
            x = apply_aug(A.ShiftScaleRotate(
                shift_limit=0.0625,
                scale_limit=0.1,
                rotate_limit=10,
                p=1.0), x)

        if self.normalize:
            x = apply_aug(A.Normalize(
                (0.485, 0.456, 0.406),
                (0.229, 0.224, 0.225),
                max_pixel_value=1.,
                always_apply=True
            ), x)

        x = x.astype(np.float32)
        x = np.transpose(x, (2, 0, 1))
        
        if self.train:
            # y = y.astype(np.int64)
            return x
        else:
            return x
