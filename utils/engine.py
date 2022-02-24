from math import ceil
import torch
import numpy as np
import math
import os
from utils.cal_tools import IouCal, AverageMeter, ProgressMeter


def train_model(args, epoch, model, train_loader, criterion, optimizer, writer, device):
    model.train()
    train_main_loss = AverageMeter('Train Main Loss', ':.4')
    train_aux_loss = AverageMeter('Train Aux Loss', ':.4')
    progress = ProgressMeter(len(train_loader), [train_main_loss, train_aux_loss], prefix="Epoch: [{}]".format(epoch))
    curr_iter = (epoch - 1) * len(train_loader)

    for i_batch, data in enumerate(train_loader):
        inputs = data["images"].to(device, dtype=torch.float32)
        mask = data["masks"].to(device, dtype=torch.int64)

        outputs, aux = model(inputs)
        main_loss = criterion(outputs, mask)
        aux_loss = criterion(aux, mask)

        loss = main_loss + 0.4 * aux_loss

        train_main_loss.update(main_loss.item)
        train_aux_loss.update(aux_loss.item)

        writer.add_scalar('train_main_loss', train_main_loss.avg, curr_iter)
        writer.add_scalar('train_aux_loss', train_aux_loss.avg, curr_iter)
        writer.add_scalar('lr', optimizer.param_groups[1]['lr'], curr_iter)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        curr_iter += 1

        if i_batch % args.print_freq == 0:
            progress.display(i_batch)


def evaluation(args, epoch, model, val_loader, criterion, device):
    model.eval()
    val_loss = AverageMeter('Val Main Loss', ':.4')
    progress = ProgressMeter(len(val_loader), [val_loss], prefix="Epoch: [{}]".format(epoch))
    iou = IouCal(args)
    for i_batch, data in enumerate(val_loader):
        inputs = data["images"].to(device, dtype=torch.float32)
        mask = data["masks"].to(device, dtype=torch.int64)

        pred = inference_sliding(args, model, inputs, args.crop_size, args.classes, args.stride_rate)
        iou.evaluate(pred, mask)
        val_loss.update(criterion(pred, mask).item)

        if i_batch % 1 == 0:
            progress.display(i_batch)

    acc, acc_cls, mean_iou = iou.iou_demo()

    if val_loss.avg < args['best_record']['val_loss']:
        args['best_record']['val_loss'] = val_loss.avg
        args['best_record']['epoch'] = epoch
        args['best_record']['acc'] = acc
        args['best_record']['acc_cls'] = acc_cls
        args['best_record']['mean_iou'] = mean_iou
        torch.save(model.state_dict(), os.path.join(args.output_dir, "_epoch", str(epoch), "_PSPNet.pt"))

    print('-----------------------------------------------------------------------------------------------------------')
    print('[epoch %d], [val loss %.5f], [acc %.5f], [acc_cls %.5f], [mean_iou %.5f]' % (
        epoch, val_loss.avg, acc, acc_cls, mean_iou))

    print('best record: [val loss %.5f], [acc %.5f], [acc_cls %.5f], [mean_iou %.5f], ---- [epoch %d], '
          % (args['best_record']['val_loss'], args['best_record']['acc'],
                    args['best_record']['acc_cls'], args['best_record']['mean_iou'], args['best_record']['epoch']))

    print('-----------------------------------------------------------------------------------------------------------')


@torch.no_grad()
def inference_sliding(args, model, image, crop_size, classes, stride_rate=0.5):
    image_size = image.size()
    stride = int(math.ceil(crop_size[0] * stride_rate))
    tile_rows = ceil((image_size[2]-crop_size)/stride)
    tile_cols = ceil((image_size[3]-crop_size)/stride)
    b = image_size[0]

    full_probs = torch.from_numpy(np.zeros((b, classes, image_size[2], image_size[3]))).to(args.device)
    count_predictions = torch.from_numpy(np.zeros((b, classes, image_size[2], image_size[3]))).to(args.device)

    for row in range(tile_rows):
        for col in range(tile_cols):
            x1 = int(col * stride)
            y1 = int(row * stride)
            x2 = x1 + crop_size
            y2 = y1 + crop_size
            if row == tile_rows - 1:
                y2 = image_size[2]
                y1 = image_size[2] - crop_size
            if col == tile_cols - 1:
                x2 = image_size[3]
                x1 = image_size[3] - crop_size

            img = image[:, :, y1:y2, x1:x2]

            with torch.set_grad_enabled(False):
                padded_prediction = model(img)
                if isinstance(padded_prediction, tuple):
                    padded_prediction = padded_prediction[0]
            count_predictions[:, :, y1:y2, x1:x2] += 1
            full_probs[:, :, y1:y2, x1:x2] += padded_prediction  # accumulate the predictions

    # average the predictions in the overlapping regions
    full_probs /= count_predictions
    _, preds = torch.max(full_probs, 1)
    return preds