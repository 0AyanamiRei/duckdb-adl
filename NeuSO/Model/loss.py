import torch
import torch.nn.functional as F
from Utils.util import log_transform

def mse_loss(output, target):
    return F.mse_loss(output, target)

def log_loss(output, target):
    target = torch.log(target)
    return F.mse_loss(output, target)
