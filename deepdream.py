"""
    This file contains the implementation of the DeepDream algorithm.

    If you have problems understanding any parts of the code,
    go ahead and experiment with functions in the playground.py file.
"""

import os
import argparse
import shutil
import time


import numpy as np
import torch
import cv2 as cv


import utils.utils as utils
from utils.constants import *

# loss.backward(layer) <- original implementation did it like this it's equivalent to MSE(reduction='sum')/2
def gradient_ascent(config, model, input_tensor, layer_ids_to_use, iteration):
    # Step 0: Feed forward pass
    out = model(input_tensor,2)

    # Step 1: Grab activations/feature maps of interest
    activations = [out[layer_id_to_use] for layer_id_to_use in layer_ids_to_use]

    # Step 2: Calculate loss over activations
    losses = []
    for layer_activation in activations:
        # Use torch.norm(torch.flatten(layer_activation), p) with p=2 for L2 loss and p=1 for L1 loss.
        # But I'll use the MSE as it works really good, I didn't notice any serious change when going to L1/L2.
        # using torch.zeros_like as if we wanted to make activations as small as possible but we'll do gradient ascent
        # and that will cause it to actually amplify whatever the network "sees" thus yielding the famous DeepDream look
        # loss_component = torch.nn.MSELoss(reduction='mean')(layer_activation, torch.zeros_like(layer_activation))
        loss_component = layer_activation
        losses.append(loss_component)

    loss = torch.mean(torch.stack(losses))
    loss.backward()

    # Step 3: Process image gradients (smoothing + normalization)
    grad = input_tensor.grad.data

    # Applies 3 Gaussian kernels and thus "blurs" or smoothens the gradients and gives visually more pleasing results
    # sigma is calculated using an arbitrary heuristic feel free to experiment
    sigma = ((iteration + 1) / config['num_gradient_ascent_iterations']) * 2.0 + config['smoothing_coefficient']
    smooth_grad = utils.CascadeGaussianSmoothing(kernel_size=9, sigma=sigma)(grad)  # "magic number" 9 just works well

    # Normalize the gradients (make them have mean = 0 and std = 1)
    # I didn't notice any big difference normalizing the mean as well - feel free to experiment
    g_std = torch.std(smooth_grad)
    g_mean = torch.mean(smooth_grad)
    smooth_grad = smooth_grad - g_mean
    smooth_grad = smooth_grad / g_std

    # Step 4: Update image using the calculated gradients (gradient ascent step)
    input_tensor.data += config['lr'] * smooth_grad

    # Step 5: Clear gradients and clamp the data (otherwise values would explode to +- "infinity")
    input_tensor.grad.data.zero_()
    input_tensor.data = torch.max(torch.min(input_tensor, UPPER_IMAGE_BOUND), LOWER_IMAGE_BOUND)


def deep_dream_static_image(config, img):
    model = utils.fetch_and_prepare_model(config['model_name'], config['pretrained_weights'], DEVICE)
    
    try:
        layer_ids_to_use = [model.layer_names.index(layer_name) for layer_name in config['layers_to_use']]
    except Exception as e:  # making sure you set the correct layer name for this specific model
        print(f'Invalid layer names {[layer_name for layer_name in config["layers_to_use"]]}.')
        print(f'Available layers for model {config["model_name"]} are {model.layer_names}.')
        return

    if img is None:  # load either the provided image or start from a pure noise image
        img_path = utils.parse_input_file(config['input'])
        # load a numpy, [0, 1] range, channel-last, RGB image
        img = utils.load_image(img_path, target_shape=config['img_width'])
        if config['use_noise']:
            shape = img.shape
            img = np.random.uniform(low=0.0, high=1.0, size=shape).astype(np.float32)

    img = utils.pre_process_numpy_img(img)
    base_shape = img.shape[:-1]  # save initial height and width
    # Note: simply rescaling the whole result (and not only details, see original implementation) gave me better results
    # Going from smaller to bigger resolution (from pyramid top to bottom)
    for pyramid_level in range(config['pyramid_size']):
        new_shape = utils.get_new_shape(config, base_shape, pyramid_level)
        img = cv.resize(img, (new_shape[1], new_shape[0]))
        input_tensor = utils.pytorch_input_adapter(img, DEVICE)
        for iteration in range(config['num_gradient_ascent_iterations']):
            h_shift, w_shift = np.random.randint(-config['spatial_shift_size'], config['spatial_shift_size'] + 1, 2)
            input_tensor = utils.random_circular_spatial_shift(input_tensor, h_shift, w_shift)
            gradient_ascent(config, model, input_tensor, layer_ids_to_use, iteration)
            input_tensor = utils.random_circular_spatial_shift(input_tensor, h_shift, w_shift, should_undo=True)
        img = utils.pytorch_output_adapter(input_tensor)
    return utils.post_process_numpy_img(img)


if __name__ == "__main__":

    # Only a small subset is exposed by design to avoid cluttering
    parser = argparse.ArgumentParser()

    # Common params
    parser.add_argument("--input", type=str, help="Input IMAGE or VIDEO name that will be used for dreaming", default='noise.jpg')
    parser.add_argument("--img_width", type=int, help="Resize input image to this width", default=224)
    parser.add_argument("--layers_to_use", type=str, nargs='+', help="Layer whose activations we should maximize while dreaming", default=['fc7'])
    parser.add_argument("--model_name", choices=[m.name for m in SupportedModels],
                        help="Neural network (model) to use for dreaming", default=SupportedModels.ALEXNET.name)

    # Main params for experimentation (especially pyramid_size and pyramid_ratio)
    parser.add_argument("--pyramid_ratio", type=float, help="Ratio of image sizes in the pyramid", default=1.8)
    parser.add_argument("--lr", type=float, help="Learning rate i.e. step size in gradient ascent", default=0.1)

    # You usually won't need to change these as often
    parser.add_argument("--should_display", action='store_true', help="Display intermediate dreaming results (default False)")
    parser.add_argument("--spatial_shift_size", type=int, help='Number of pixels to randomly shift image before grad ascent', default=32)
    parser.add_argument("--smoothing_coefficient", type=float, help='Directly controls standard deviation for gradient smoothing', default=0.5)
    parser.add_argument("--use_noise", action='store_true', help="Use noise as a starting point instead of input image (default False)")
    args = parser.parse_args()

    # Wrapping configuration into a dictionary
    config = dict()
    for arg in vars(args):
        config[arg] = getattr(args, arg)
    config['dump_dir'] = OUT_IMAGES_PATH
    config['input_name'] = os.path.basename(config['input'])

    all_models = os.listdir(WEIGHT_DIR_PATH)
    for model_now in all_models:
        config['pretrained_weights']=model_now
        config['dump_dir'] = os.path.join(config['dump_dir'], f'{config["pretrained_weights"][0:-4]}')

        print('Dreaming started!')
        for pp in range(1,3):
            for ii in range(10,101,50):
                config['pyramid_size']=pp
                config['num_gradient_ascent_iterations']=ii
                
                img = deep_dream_static_image(config, img=None)  # img=None -> will be loaded inside of deep_dream_static_image
                dump_path = utils.save_and_maybe_display_image(config, img)
                print(f'Saved DeepDream static image to: {os.path.relpath(dump_path)}\n')