import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np


class SegmentationLosses(object):
    def __init__(self, weight=None, size_average=True, batch_average=True, ignore_index=255, cuda=False):
        self.ignore_index = ignore_index
        self.weight = weight
        self.size_average = size_average
        self.batch_average = batch_average
        self.cuda = cuda

    def build_loss(self, mode='ce'):
        """Choices: ['ce' or 'focal']"""
        if mode == 'ce':
            return self.CrossEntropyLoss
        elif mode == 'focal':
            return self.FocalLoss
        elif mode == 'ce_finetune':
            return self.CrossEntropyLossFinetune
        else:
            raise NotImplementedError

    def CrossEntropyLoss(self, logit, target):
        n, c, h, w = logit.size()
        criterion = nn.CrossEntropyLoss(weight=self.weight, ignore_index=self.ignore_index,
                                        size_average=self.size_average)
        if self.cuda:
            criterion = criterion.cuda()

        loss = criterion(logit, target.long())

        if self.batch_average:
            loss /= n

        return loss


    def CrossEntropyLossFinetune(self, logit, target):
        criterion = nn.CrossEntropyLoss(ignore_index=self.ignore_index, size_average=self.size_average)
        if self.cuda:
            criterion = criterion.cuda()

        loss = criterion(logit, target.long())

        if self.batch_average:
            loss /= logit.shape[0]

        return loss


    def FocalLoss(self, logit, target, gamma=2, alpha=0.5):
        n, c, h, w = logit.size()
        criterion = nn.CrossEntropyLoss(weight=self.weight, ignore_index=self.ignore_index,
                                        size_average=self.size_average)
        if self.cuda:
            criterion = criterion.cuda()

        logpt = -criterion(logit, target.long())
        pt = torch.exp(logpt)
        if alpha is not None:
            logpt *= alpha
        loss = -((1 - pt) ** gamma) * logpt

        if self.batch_average:
            loss /= n

        return loss




def EntropyLoss(v):
    """
        Entropy loss for probabilistic prediction vectors
        input: batch_size x channels x h x w
        output: batch_size x 1 x h x w
    """
    assert v.dim() == 4
    n, c, h, w = v.size()
    v_soft = F.softmax(v)
    return -torch.sum(torch.mul(v_soft, torch.log2(v_soft + 1e-30))) / (n * h * w * np.log2(c))


def EntropyLossMasked(v, mask):
    """
        Entropy loss for probabilistic prediction vectors
        input: batch_size x channels x h x w
        output: batch_size x 1 x h x w
    """
    assert v.dim() == 4
    v = v.permute(0, 2, 3, 1)[mask]

    n, c = v.size()
    v_soft = F.softmax(v)
    return -torch.sum(torch.mul(v_soft, torch.log2(v_soft + 1e-30))) / (n * np.log2(c))


class GMMNLoss(object):

    def __init__(self, sigma=[2, 5, 10, 20, 40, 80], cuda=False):
        self.sigma = sigma
        self.cuda = cuda

    def build_loss(self):
        return self.moment_loss


    def get_scale_matrix(self, M, N):
        s1 = (torch.ones((N, 1)) * 1.0 / N)
        s2 = (torch.ones((M, 1)) * -1.0 / M)
        if self.cuda:
            s1, s2 = s1.cuda(), s2.cuda()
        return torch.cat((s1, s2), 0)

    def moment_loss(self, gen_samples, x):
        X = torch.cat((gen_samples, x), 0)
        XX = torch.matmul(X, X.t())
        X2 = torch.sum(X * X, 1, keepdim=True)
        exp = XX - 0.5 * X2 - 0.5 * X2.t()
        M = gen_samples.size()[0]
        N = x.size()[0]
        s = self.get_scale_matrix(M, N)
        S = torch.matmul(s, s.t())

        loss = 0
        for v in self.sigma:
            kernel_val = torch.exp(exp / v)
            loss += torch.sum(S * kernel_val)

        loss = torch.sqrt(loss)
        return loss



class MSELoss(object):

    def build_loss(self):
        return self.mse_loss

    def mse_loss(self, score, target, target_embed):
        """Mean Square Vector between two (n,c,h,w) volumes (score and target).
        ARGS
          score: (n, c, h, w)
          target: (n, h, w)
          target_embed: (n, c, h, w)
        RET
          loss -> scalar
        """
        n, c, h, w = score.size()
        # apply mask to score and target, and turn into 1d vectors for comparision
        mask = target != 255 # ignore -1 (unknown classes); don't ignore 0 (background)
        mask_size = mask.data.sum()
        mask_tensor = mask.view(n,1,h,w).repeat(1,c,1,1)
        score_masked = score[mask_tensor]
        target_embed_masked = target_embed[mask_tensor]
        # # calculate loss on masked score and target
        # same as: loss = (torch.sum((score_masked - target_embed_masked)**2))
        loss = F.mse_loss(score_masked, target_embed_masked, size_average=False)
        loss /= mask_size
        return loss


class MSELoss_bis(object):

    def build_loss(self):
        return self.mse_loss

    def mse_loss(self, prediction, target, target_embed):
        """Mean Square Vector between two (n,c,h,w) volumes (score and target.view(-1)target).
        ARGS
          score: (n, c, h, w)
          target: (n, h, w)
          target_embed: (n, c, h, w)
        RET
          loss -> scalar
        """
        _, c, _, _ = target_embed.size()
        prediction = prediction.permute((0, 2, 3, 1)).contiguous().view((-1, c))
        target_embed = target_embed.permute((0, 2, 3, 1)).contiguous().view((-1, c))
        target = target.view(-1)
        mask = target != 255
        if mask.sum() > 0:
            prediction_masked = prediction[mask]
            target_embed_masked = target_embed[mask]
            loss = F.mse_loss(prediction_masked, target_embed_masked, size_average=True)
        else:
            loss = 0.0
        return loss



class CosineLoss(object):

    def build_loss(self):
        return self.cosine_loss

    def cosine_loss(self, score, target, target_embed):
        """Negative Cosine Similarity Loss between two (n,c,h,w) volumes (score and target).
        ARGS
          score: (n, c, h, w)
          target: (n, h, w)
          target_embed: (n, c, h, w)
        RET
          loss -> scalar
        """
        n, c, h, w = score.size()

        # normalize score and target
        score_norm = torch.norm(score, p=2, dim=1, keepdim=True)
        score = score / score_norm

        target_embed_norm = torch.norm(target_embed, p=2, dim=1, keepdim=True)
        target_embed = target_embed/ target_embed_norm

        # apply mask to score and target
        mask = target != 255 # ignore -1 (unknown classes); don't ignore 0 (background)
        mask_size = mask.data.sum()
        mask_tensor = mask.view(n,1,h,w).repeat(1,c,1,1)
        score_masked = score[mask_tensor]
        target_embed_masked = target_embed[mask_tensor]

        loss = mask_size - torch.sum(score_masked * target_embed_masked)
        loss /= mask_size # divide loss by number of non-masked pixels
        return loss


class CosineLoss_bis(object):

    def build_loss(self):
        return self.cosine_loss

    def cosine_loss(self, prediction, target, target_embed):
        """Negative Cosine Similarity Loss between two (n,c,h,w) volumes (score and target).
        ARGS
          score: (n, c, h, w)
          target: (n, h, w)
          target_embed: (n, c, h, w)
        RET
          loss -> scalar
        """
        _, c, _, _ = target_embed.size()
        prediction = prediction.permute((0, 2, 3, 1)).contiguous().view((-1, c))
        target_embed = target_embed.permute((0, 2, 3, 1)).contiguous().view((-1, c))
        target = target.view(-1)
        mask = target != 255
        if mask.sum() > 0:
            prediction_masked = prediction[mask]
            target_embed_masked = target_embed[mask]
            prediction_masked = F.normalize(prediction_masked - torch.min(prediction_masked, dim=1, keepdim=True)[0], p=2, dim=1)
            target_embed_masked = F.normalize(target_embed_masked - torch.min(target_embed_masked, dim=1, keepdim=True)[0], p=2, dim=1)
            loss = (1 - torch.nn.functional.cosine_similarity(prediction_masked, target_embed_masked, dim=1, eps=1e-8)).mean()
        else:
            loss = 0.0
        return loss



if __name__ == "__main__":
    loss = SegmentationLosses(cuda=True)
    a = torch.rand(1, 3, 7, 7).cuda()
    b = torch.rand(1, 7, 7).cuda()
    print(loss.CrossEntropyLoss(a, b).item())
    print(loss.FocalLoss(a, b, gamma=0, alpha=None).item())
    print(loss.FocalLoss(a, b, gamma=2, alpha=0.5).item())




