import torch.nn as nn
from torch.nn.functional import dropout


# output a positive number, representing log of cost, card,...
class MLP(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim):
        super(MLP, self).__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, output_dim)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2)
        self.softplus = nn.Softplus()
        self.init_weights()

    def init_weights(self):
        nn.init.kaiming_uniform_(self.fc1.weight, a=0.05, mode='fan_in', nonlinearity='leaky_relu')
        nn.init.kaiming_uniform_(self.fc2.weight, a=0.05, mode='fan_in', nonlinearity='leaky_relu')

    def forward(self, x):
        x = self.fc1(x)
        x = self.lrelu(x)
        return self.fc2(x)