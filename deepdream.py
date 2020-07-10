import os
import time

import matplotlib.pyplot as plt
import numpy as np
import scipy.ndimage as nd
import torch
from torchvision import transforms
import cv2 as cv

from collections import namedtuple
from torchvision import models

IMAGENET_MEAN_1 = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD_1 = np.array([0.229, 0.224, 0.225], dtype=np.float32)
IMAGENET_MEAN_255 = np.array([123.675, 116.28, 103.53], dtype=np.float32)
# Usually when normalizing 0..255 images only mean-normalization is performed -> that's why standard dev is all 1s here
IMAGENET_STD_NEUTRAL = np.array([1, 1, 1], dtype=np.float32)

# todo: add support for static input image
# todo: experiment with different models (GoogLeNet, pytorch models trained on MIT Places?)
# todo: experiment with different single/multiple layers
# todo: experiment with different objective functions (L2, guide, etc.)

# todo: try out Adam on -L

# todo: add support for video (simple affine transform)
# todo: add playground function for understanding PyTorch gradients


# https://stackoverflow.com/questions/37119071/scipy-rotate-and-zoom-an-image-without-changing-its-dimensions/48097478#48097478
def create_image_pyramid(img, num_octaves, octave_scale):
    img_pyramid = [img]
    for i in range(num_octaves-1):  # img_pyramid will have "num_octaves" images
        img_pyramid.append(cv.resize(img_pyramid[-1], (0, 0), fx=octave_scale, fy=octave_scale))
    return img_pyramid


def random_circular_spatial_shift(img, x, y):
    print('to be implemented')


def initial_playground():
    from sklearn.datasets import load_sample_image
    china = load_sample_image("china.jpg")
    octave_scale = 1.4
    c2 = nd.zoom(china, (1.0 / octave_scale, 1.0 / octave_scale, 1), order=1)
    print(china.shape, c2.shape)

    # plt.imshow(china)
    # plt.show()
    # plt.imshow(c2)
    # plt.show()

    jitter = 32

    ox, oy = np.random.randint(-jitter, jitter + 1, 2)

    # china = np.roll(np.roll(china, ox, 1), oy, 2)
    # plt.imshow(china)
    # plt.show()


def tensor_summary(t):
    print(f'data={t.data}')
    print(f'requires_grad={t.requires_grad}')
    print(f'grad={t.grad}')
    print(f'grad_fn={t.grad_fn}')
    print(f'is_leaf={t.is_leaf}')


# todo: explain that diff[:] is equivalent to taking MSE loss
# todo: dummy deepdream, + jitter + octaves
def play_with_pytorch_gradients():


    x = torch.tensor([[-2.0, 1.0], [1.0, 1.0]], requires_grad=True)
    y = x + 2
    z = y * y * 3
    out = z.mean()
    out.backward()

    tensor_summary(x)
    tensor_summary(y)
    tensor_summary(z)
    tensor_summary(out)

    # On calling backward(), gradients are populated only for the nodes which have both requires_grad and is_leaf True.
    # Remember, the backward graph is already made dynamically during the forward pass.
    # graph of Function objects (the .grad_fn attribute of each torch.Tensor is an entry point into this graph)
    # Function class ha 2 member functions: 1) forward 2) backward
    # whatever comes from the front layers to current node is saved in grad attribute of the current node
    # backward is usually called on L-node with unit tensor because dL/L = 1


def load_image(img_path, target_shape=None):
    if not os.path.exists(img_path):
        raise Exception(f'Path does not exist: {img_path}')
    img = cv.imread(img_path)[:, :, ::-1]  # [:, :, ::-1] converts BGR (opencv format...) into RGB

    if target_shape is not None:  # resize section
        if isinstance(target_shape, int) and target_shape != -1:  # scalar -> implicitly setting the width
            current_height, current_width = img.shape[:2]
            new_width = target_shape
            new_height = int(current_height * (new_width / current_width))
            img = cv.resize(img, (new_width, new_height), interpolation=cv.INTER_CUBIC)
        else:  # set both dimensions to target shape
            img = cv.resize(img, (target_shape[1], target_shape[0]), interpolation=cv.INTER_CUBIC)

    # this need to go after resizing - otherwise cv.resize will push values outside of [0,1] range
    img = img.astype(np.float32)  # convert from uint8 to float32
    img /= 255.0  # get to [0, 1] range
    return img


def prepare_img(img_path, target_shape, device, batch_size=1, should_normalize=True, is_255_range=False):
    img = load_image(img_path, target_shape=target_shape)

    transform_list = [transforms.ToTensor()]
    if is_255_range:
        transform_list.append(transforms.Lambda(lambda x: x.mul(255)))
    if should_normalize:
        transform_list.append(transforms.Normalize(mean=IMAGENET_MEAN_255, std=IMAGENET_STD_NEUTRAL) if is_255_range else transforms.Normalize(mean=IMAGENET_MEAN_1, std=IMAGENET_STD_1))
    transform = transforms.Compose(transform_list)

    img = transform(img).to(device)
    img = img.repeat(batch_size, 1, 1, 1)

    return img


def pytorch_input_adapter(img, device):
    tensor = transforms.ToTensor()(img).to(device).unsqueeze(0)
    tensor.requires_grad = True
    return tensor


def pytorch_output_adapter(img):
    return np.moveaxis(img.to('cpu').detach().numpy()[0], 0, 2)


class Vgg16(torch.nn.Module):
    """Only those layers are exposed which have already proven to work nicely."""
    def __init__(self, requires_grad=False, show_progress=False):
        super().__init__()
        # Keeping eval() mode only for consistency - it only affects BatchNorm and Dropout both of which we won't use
        vgg16 = models.vgg16(pretrained=True, progress=show_progress).eval()
        vgg_pretrained_features = vgg16.features
        self.layer_names = ['relu1_2', 'relu2_2', 'relu3_3', 'relu4_3']

        self.slice1 = torch.nn.Sequential()
        self.slice2 = torch.nn.Sequential()
        self.slice3 = torch.nn.Sequential()
        self.slice4 = torch.nn.Sequential()
        for x in range(4):
            self.slice1.add_module(str(x), vgg_pretrained_features[x])
        for x in range(4, 9):
            self.slice2.add_module(str(x), vgg_pretrained_features[x])
        for x in range(9, 16):
            self.slice3.add_module(str(x), vgg_pretrained_features[x])
        for x in range(16, 23):
            self.slice4.add_module(str(x), vgg_pretrained_features[x])

        # Set these to False so that PyTorch won't be including them in it's autograd engine - eating up precious memory
        if not requires_grad:
            for param in self.parameters():
                param.requires_grad = False

    def forward(self, x):
        x = self.slice1(x)
        relu1_2 = x
        x = self.slice2(x)
        relu2_2 = x
        x = self.slice3(x)
        relu3_3 = x
        x = self.slice4(x)
        relu4_3 = x
        vgg_outputs = namedtuple("VggOutputs", self.layer_names)
        out = vgg_outputs(relu1_2, relu2_2, relu3_3, relu4_3)
        return out


def preprocess(img):
    img = (img - IMAGENET_MEAN_1) / IMAGENET_STD_1
    return img


def post_process_image(dump_img, channel_last=False):
    assert isinstance(dump_img, np.ndarray), f'Expected numpy image got {type(dump_img)}'

    if channel_last:
        dump_img = np.moveaxis(dump_img, 2, 0)

    mean = IMAGENET_MEAN_1.reshape(-1, 1, 1)
    print('mean shape', mean.shape)
    std = IMAGENET_STD_1.reshape(-1, 1, 1)
    dump_img = (dump_img * std) + mean  # de-normalize
    dump_img = (np.clip(dump_img, 0., 1.) * 255).astype(np.uint8)
    dump_img = np.moveaxis(dump_img, 0, 2)

    return dump_img


def save_and_maybe_display_image(dump_img, should_display=True, channel_last=False, name='test.jpg'):
    assert isinstance(dump_img, np.ndarray), f'Expected numpy array got {type(dump_img)}.'

    dump_img = post_process_image(dump_img, channel_last=channel_last)
    cv.imwrite(name, dump_img[:, :, ::-1])  # ::-1 because opencv expects BGR (and not RGB) format...

    if should_display:
        plt.imshow(dump_img)
        plt.show()


lower_bound = torch.tensor((-IMAGENET_MEAN_1/IMAGENET_STD_1).reshape(1, -1, 1, 1)).to('cuda')
upper_bound = torch.tensor(((1-IMAGENET_MEAN_1)/IMAGENET_STD_1).reshape(1, -1, 1, 1)).to('cuda')


def gradient_ascent(backbone_network, img, lr):
    out = backbone_network(img)
    layer = out.relu4_3
    layer.backward(layer)

    g = img.grad.data

    # todo: gaussian filtering on gradient

    g_mean = torch.mean(torch.abs(g))
    img.data += lr * (g / g_mean)
    img.data = torch.max(torch.min(img, upper_bound), lower_bound)
    img.grad.data.zero_()

    # todo: try 2 policies: 1) clip 2) rescale
    # tmp_img = img.detach().to('cpu').numpy()[0]
    # tmp_img = np.clip(tmp_img, (-IMAGENET_MEAN_1/IMAGENET_STD_1).reshape(-1, 1, 1), ((1-IMAGENET_MEAN_1)/IMAGENET_STD_1).reshape(-1, 1, 1))

    # with torch.no_grad():
    #     print(torch.max(img), torch.min(img))

    # img = torch.max(torch.min(img, upper_bound), lower_bound)
    # https://stackoverflow.com/questions/54738045/column-dependent-bounds-in-torch-clamp
    # print(clipped.shape)
    # print(torch.max(img, dim=1).shape)

    # tmp1 = pytorch_output_adapter(clipped)
    # tmp2 = pytorch_output_adapter(img)
    # print(np.min(tmp1), np.max(tmp2))


# no spatial jitter, no octaves, no clipping policy, no advanced gradient normalization (std)
def simple_deep_dream(img_path):
    img_path = 'figures.jpg'
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    img = prepare_img(img_path, target_shape=500, device=device)
    img.requires_grad = True
    backbone_network = Vgg16(requires_grad=False).to(device)

    n_iter = 2
    lr = 0.2

    for iter in range(n_iter):
        gradient_ascent(backbone_network, img, lr)

    img = img.to('cpu').detach().numpy()[0]
    save_and_maybe_display_image(img)


# no spatial jitter, no advanced gradient normalization (std), no clipping
def deep_dream(img):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    net = Vgg16(requires_grad=False).to(device)
    base_img = preprocess(img)

    # todo: experiment with these
    pyramid_size = 4
    pyramid_ratio = 1./1.4
    n_iter = 10
    lr = 0.09

    # contains pyramid_size copies of the very same image with different resolutions
    img_pyramid = create_image_pyramid(base_img, pyramid_size, pyramid_ratio)
    for img in img_pyramid:
        print(img.shape)

    detail = np.zeros_like(img_pyramid[-1])  # allocate image for network-produced details

    best_img = []
    # going from smaller to bigger resolution
    # todo: [1] see whether jitter helps
    # todo: try out advanced gradient scaling
    for octave, octave_base in enumerate(reversed(img_pyramid)):
        h, w = octave_base.shape[:2]
        if octave > 0:
            # upscale details from the previous octave
            h1, w1 = detail.shape[:2]
            detail = cv.resize(detail, (w, h))
        input_img = octave_base + detail
        input_tensor = pytorch_input_adapter(input_img, device)
        for i in range(n_iter):
            gradient_ascent(net, input_tensor, lr)

            # visualization
            # current_img = pytorch_output_adapter(input_tensor)
            # print(current_img.shape)
            # vis = post_process_image(current_img, channel_last=True)
            # plt.imshow(vis); plt.show()

        detail = pytorch_output_adapter(input_tensor) - octave_base

        current_img = pytorch_output_adapter(input_tensor)
        best_img = current_img
        # save_and_maybe_display_image(current_img, channel_last=True)
    return best_img


# rotation:
# zoom: [1-s,1-s,1] [h*s/2,w*s/2,0]
# vertical stretch:  [1-s,1,1], [h*s/2,0,0]
# note: don't use scipy.ndimage it's way slower than OpenCV
# todo: make a set of interesting transforms in OpenCV (e.g. spiral-zoom motion)
def understand_affine():
    h, w, c = [500, 500, 3]
    s = 0.05

    img = np.zeros((h, w, c))
    img[100:400, 100:400] = 1.0

    matrix = np.asarray([0.95, 0.95, 1])

    transformed_img = img
    deg = 3
    theta = (deg / 180) * np.pi
    matrix = np.asarray([[np.cos(theta), -np.sin(theta), 0],
                        [np.sin(theta), np.cos(theta), 0],
                        [0., 0., 1.]])
    zoom_matrix = np.asarray([[1-s, 0, 0],
                        [0, 1-s, 0],
                        [0., 0., 1.]])
    ts = time.time()
    for i in range(10):
        transformed_img = nd.affine_transform(transformed_img, zoom_matrix, [h*s/2,w*s/2,0], order=1)
        # transformed_img = cv.warpPerspective(transformed_img, zoom_matrix, (w, h))
        # plt.imshow(np.hstack([img, transformed_img])); plt.show()

    print(f'{(time.time()-ts)*1000} ms')
    plt.imshow(np.hstack([img, transformed_img]));
    plt.show()


def deep_dream_video(img_path):
    frame = load_image(img_path, target_shape=600)

    s = 0.05  # scale coefficient
    for i in range(100):
        frame = deep_dream(frame)
        h, w = frame.shape[:2]
        save_and_maybe_display_image(frame, channel_last=True, should_display=False, name=os.path.join('video', str(i) + '.jpg'))
        # todo: understand how affine transform works here and make it declarative, rotate, zoom, etc. nice API
        frame = nd.affine_transform(frame, np.asarray([1 - s, 1 - s, 1]), [h * s / 2, w * s / 2, 0.0], order=1)


if __name__ == "__main__":
    # play_with_pytorch_gradients()

    img_path = 'figures.jpg'
    # frame = load_image(img_path, target_shape=1024)
    # stylized = deep_dream(frame)
    # save_and_maybe_display_image(stylized, channel_last=True, name='test.jpg')
    deep_dream_video(img_path)

    # deep_dream_video(img_path)
    # understand_affine()