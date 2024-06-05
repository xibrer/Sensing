import torch
import torch.nn as nn
from torchinfo import summary


class GlobalLayerNorm(nn.Module):
    '''
       Calculate Global Layer Normalization
       dim: (int or list or torch.Size) –
            input shape from an expected input of size
       eps: a value added to the denominator for numerical stability.
       elementwise_affine: a boolean value that when set to True, 
           this module has learnable per-element affine parameters 
           initialized to ones (for weights) and zeros (for biases).
    '''

    def __init__(self, dim, eps=1e-05, elementwise_affine=True):
        super(GlobalLayerNorm, self).__init__()
        self.dim = dim
        self.eps = eps
        self.elementwise_affine = elementwise_affine

        if self.elementwise_affine:
            self.weight = nn.Parameter(torch.ones(self.dim, 1))
            self.bias = nn.Parameter(torch.zeros(self.dim, 1))
        else:
            self.register_parameter('weight', None)
            self.register_parameter('bias', None)

    def forward(self, x):
        # x = N x C x L
        # N x 1 x 1
        # cln: mean,var N x 1 x L
        # gln: mean,var N x 1 x 1
        if x.dim() != 3:
            raise RuntimeError("{} accept 3D tensor as input".format(
                self.__name__))

        mean = torch.mean(x, (1, 2), keepdim=True)
        var = torch.mean((x - mean) ** 2, (1, 2), keepdim=True)
        # N x C x L
        if self.elementwise_affine:
            x = self.weight * (x - mean) / torch.sqrt(var + self.eps) + self.bias
        else:
            x = (x - mean) / torch.sqrt(var + self.eps)
        return x


class CumulativeLayerNorm(nn.LayerNorm):
    '''
       Calculate Cumulative Layer Normalization
       dim: you want to norm dim
       elementwise_affine: learnable per-element affine parameters 
    '''

    def __init__(self, dim, elementwise_affine=True):
        super(CumulativeLayerNorm, self).__init__(
            dim, elementwise_affine=elementwise_affine)

    def forward(self, x):
        # x: N x C x L
        # N x L x C
        x = torch.transpose(x, 1, 2)
        # N x L x C == only channel norm
        x = super().forward(x)
        # N x C x L
        x = torch.transpose(x, 1, 2)
        return x


def select_norm(norm, dim):
    if norm == 'gln':
        return GlobalLayerNorm(dim, elementwise_affine=True)
    if norm == 'cln':
        return CumulativeLayerNorm(dim, elementwise_affine=True)
    else:
        return nn.BatchNorm1d(dim)


class Encoder(nn.Module):

    def __init__(self, in_channels, out_channels, kernel_size, stride):
        super(Encoder, self).__init__()
        self.sequential = nn.Sequential(
            Conv1D(in_channels, out_channels, kernel_size, stride=stride),
            Conv1D(out_channels, out_channels, kernel_size=3, stride=1, padding=1),
            nn.PReLU(),
            Conv1D(out_channels, out_channels, kernel_size=3, stride=1, padding=1),
            nn.PReLU(),
            Conv1D(out_channels, out_channels, kernel_size=3, stride=1, padding=1),
            nn.PReLU()
        )

    def forward(self, x):
        '''
           x: [B, T]
           out: [B, N, T]
        '''
        x = self.sequential(x)
        return x


class AccEncoder(nn.Module):

    def __init__(self, in_channels, out_channels):
        super(AccEncoder, self).__init__()
        self.sequential = nn.Sequential(
            nn.ConvTranspose1d(in_channels, out_channels, kernel_size=3, stride=2, padding=1),
            Conv1D(out_channels, out_channels, kernel_size=3, stride=1, padding=1),
            nn.PReLU(),
            # Conv1D(out_channels, out_channels, kernel_size=3, stride=1, padding=1),
            # nn.PReLU(),
            Conv1D(out_channels, out_channels, kernel_size=3, stride=1, padding=1),
            nn.PReLU()
        )

    def forward(self, x):
        '''
           x: [B, T]
           out: [B, N, T]
        '''
        x = self.sequential(x)
        return x


class Decoder(nn.Module):
    '''
        Decoder
        This module can be seen as the gradient of Conv1d with respect to its input. 
        It is also known as a fractionally-strided convolution 
        or a deconvolution (although it is not an actual deconvolution operation).
    '''

    def __init__(self, N, kernel_size=16, stride=16 // 2):
        super(Decoder, self).__init__()
        self.sequential = nn.Sequential(
            nn.ConvTranspose1d(N, N, kernel_size=3, stride=1, padding=1),
            nn.PReLU(),
            nn.ConvTranspose1d(N, N, kernel_size=3, stride=1, padding=1),
            nn.PReLU(),
            nn.ConvTranspose1d(N, N, kernel_size=3, stride=1, padding=1),
            nn.PReLU(),
            nn.ConvTranspose1d(N, 1, kernel_size=kernel_size, stride=stride, bias=True)
        )

    def forward(self, x):
        """
        x: N x L or N x C x L
        """
        x = self.sequential(x)
        if torch.squeeze(x).dim() == 1:
            x = torch.squeeze(x, dim=1)
        else:
            x = torch.squeeze(x)

        return x


class Conv1D(nn.Conv1d):
    '''
       Applies a 1D convolution over an input signal composed of several input planes.
    '''

    def __init__(self, *args, **kwargs):
        super(Conv1D, self).__init__(*args, **kwargs)

    def forward(self, x, squeeze=False):
        # x: N x C x L
        if x.dim() not in [2, 3]:
            raise RuntimeError("{} accept 2/3D tensor as input".format(
                self.__name__))
        x = super().forward(x if x.dim() == 3 else torch.unsqueeze(x, 1))
        if squeeze:
            x = torch.squeeze(x)
        return x


class ConvTrans1D(nn.ConvTranspose1d):
    '''
       This module can be seen as the gradient of Conv1d with respect to its input. 
       It is also known as a fractionally-strided convolution 
       or a deconvolution (although it is not an actual deconvolution operation).
    '''

    def __init__(self, *args, **kwargs):
        super(ConvTrans1D, self).__init__(*args, **kwargs)

    def forward(self, x, squeeze=False):
        """
        x: N x L or N x C x L
        """
        if x.dim() not in [2, 3]:
            raise RuntimeError("{} accept 2/3D tensor as input".format(
                self.__name__))
        x = super().forward(x if x.dim() == 3 else torch.unsqueeze(x, 1))
        if squeeze:
            x = torch.squeeze(x)
        return x


class Conv1D_Block(nn.Module):
    '''
       Consider only residual links
    '''

    def __init__(self, in_channels=256, out_channels=512,
                 kernel_size=3, dilation=1, norm='gln', causal=False, skip_con='True'):
        super(Conv1D_Block, self).__init__()
        # conv 1 x 1
        self.conv1x1 = Conv1D(in_channels, out_channels, 1)
        self.PReLU_1 = nn.PReLU()
        self.norm_1 = select_norm(norm, out_channels)
        # not causal don't need to padding, causal need to pad+1 = kernel_size
        self.pad = (dilation * (kernel_size - 1)) // 2 if not causal else (
                dilation * (kernel_size - 1))
        # depthwise convolution
        self.dwconv = Conv1D(out_channels, out_channels, kernel_size,
                             groups=out_channels, padding=self.pad, dilation=dilation)
        self.PReLU_2 = nn.PReLU()
        self.norm_2 = select_norm(norm, out_channels)
        self.Sc_conv = nn.Conv1d(out_channels, in_channels, 1, bias=True)
        self.Output = nn.Conv1d(out_channels, in_channels, 1, bias=True)
        self.causal = causal
        self.skip_con = skip_con

    def forward(self, x):
        # x: N x C x L
        # N x O_C x L
        c = self.conv1x1(x)
        # N x O_C x L
        c = self.PReLU_1(c)
        c = self.norm_1(c)
        # causal: N x O_C x (L+pad)
        # noncausal: N x O_C x L
        c = self.dwconv(c)
        c = self.PReLU_2(c)
        c = self.norm_2(c)
        # N x O_C x L
        if self.causal:
            c = c[:, :, :-self.pad]
        if self.skip_con:
            Sc = self.Sc_conv(c)
            c = self.Output(c)
            return Sc, c + x
        c = self.Output(c)
        return x + c


class Separation(nn.Module):
    """
       R	Number of repeats
       X	Number of convolutional blocks in each repeat
       B	Number of channels in bottleneck and the residual paths’ 1 × 1-conv blocks
       H	Number of channels in convolutional blocks
       P	Kernel size in convolutional blocks
       norm The type of normalization(gln, cl, bn)
       causal  Two choice(causal or noncausal)
       skip_con Whether to use skip connection
    """

    def __init__(self, R, X, B, H, P, norm='gln', causal=False, skip_con=True):
        super(Separation, self).__init__()
        self.separation = nn.ModuleList([])
        for r in range(R):
            for x in range(X):
                self.separation.append(Conv1D_Block(
                    B, H, P, 2 ** x, norm, causal, skip_con))
        self.skip_con = skip_con

    def forward(self, x):
        '''
           x: [B, N, L]
           out: [B, N, L]
        '''
        if self.skip_con:
            skip_connection = 0
            for i in range(len(self.separation)):
                skip, out = self.separation[i](x)
                skip_connection = skip_connection + skip
                x = out
            return skip_connection
        else:
            for i in range(len(self.separation)):
                out = self.separation[i](x)
                x = out
            return x


class ConvTasNet(nn.Module):
    '''
       ConvTasNet module
       N	Number of ﬁlters in autoencoder
       L	Length of the ﬁlters (in samples)
       B	Number of channels in bottleneck and the residual paths’ 1 × 1-conv blocks
       Sc	Number of channels in skip-connection paths’ 1 × 1-conv blocks
       H	Number of channels in convolutional blocks
       P	Kernel size in convolutional blocks
       X	Number of convolutional blocks in each repeat
       R	Number of repeats
    '''

    def __init__(self,
                 N=512,
                 L=16,
                 B=128,
                 H=512,
                 P=3,
                 X=8,
                 R=3,
                 norm="gln",
                 num_spks=2,
                 activate="relu",
                 causal=False,
                 skip_con=False):
        super(ConvTasNet, self).__init__()
        # n x 1 x T => n x N x T
        self.encoder = Encoder(1, N, L, stride=L // 2)
        self.acc_encoder = AccEncoder(1, int(N / 2))
        # n x N x T  Layer Normalization of Separation
        self.LayerN_S = select_norm('cln', N)
        self.AccLayerN_S = select_norm('cln', int(N / 2))
        # n x B x T  Conv 1 x 1 of  Separation
        self.BottleN_S = Conv1D(N, B, 1)
        self.AccBottleN_S = Conv1D(int(N / 2), B, 1)
        # Separation block
        # n x B x T => n x B x T
        self.separation = Separation(R, X, B, H, P, norm=norm, causal=causal, skip_con=skip_con)
        self.conv1d = nn.Conv1d(2*B, B, 1, 1)
        # n x B x T => n x 2*N x T
        self.gen_masks = Conv1D(B, num_spks * N, 1)
        # n x N x T => n x 1 x L
        self.decoder = Decoder(N, L, stride=L // 2)
        # activation function
        active_f = {
            'relu': nn.ReLU(),
            'sigmoid': nn.Sigmoid(),
            'softmax': nn.Softmax(dim=0)
        }
        self.activation_type = activate
        self.activation = active_f[activate]
        self.num_spks = num_spks

    def forward(self, batch):
        z, x = batch
        # x: n x 1 x L => n x N x T
        w = self.encoder(x)
        w_acc = self.acc_encoder(z)
        # n x N x L => n x B x L
        e = self.LayerN_S(w)
        e = self.BottleN_S(e)
        e_acc = self.AccLayerN_S(w_acc)
        e_acc = self.AccBottleN_S(e_acc)

        e = torch.cat((e, e_acc), dim=1)
        e = self.conv1d(e)
        # n x B x L => n x B x L
        e = self.separation(e)

        # n x B x L => n x num_spk*N x L
        m = self.gen_masks(e)
        # n x N x L x num_spks
        m = torch.chunk(m, chunks=self.num_spks, dim=1)
        # num_spks x n x N x L
        m = self.activation(torch.stack(m, dim=0))
        d = [w * m[i] for i in range(self.num_spks)]
        # decoder part num_spks x n x L
        s = [self.decoder(d[i]) for i in range(self.num_spks)]
        return s[0].unsqueeze(1)


def test_convtasnet():
    nnet = ConvTasNet()
    batch_size = 2
    summary(nnet, input_size=((batch_size, 1, 80000), (batch_size, 1, 5000)))
    # print(s.shape)


if __name__ == "__main__":
    test_convtasnet()
