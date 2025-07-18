import time, os, torch, argparse, warnings, glob

from dataLoader_multiperson import train_loader, val_loader
from utils.tools import *
from talkNet_multi import talkNet


def collate_fn_padding(data):
    audiofeatures = [item[0] for item in data]
    visualfeatures = [item[1] for item in data]
    labels = [item[2] for item in data]
    masks = [item[3] for item in data]
    cut_limit = 200
    # pad audio
    lengths = torch.tensor([t.shape[1] for t in audiofeatures])
    max_len = max(lengths)
    padded_audio = torch.stack([
        torch.cat([i, i.new_zeros((i.shape[0], max_len - i.shape[1], i.shape[2]))], 1)
        for i in audiofeatures
    ], 0)

    if max_len > cut_limit * 4:
        padded_audio = padded_audio[:, :, :cut_limit * 4, ...]

    # pad video
    lengths = torch.tensor([t.shape[1] for t in visualfeatures])
    max_len = max(lengths)
    padded_video = torch.stack([
        torch.cat([i, i.new_zeros((i.shape[0], max_len - i.shape[1], i.shape[2], i.shape[3]))], 1)
        for i in visualfeatures
    ], 0)
    padded_labels = torch.stack(
        [torch.cat([i, i.new_zeros((i.shape[0], max_len - i.shape[1]))], 1) for i in labels], 0)
    padded_masks = torch.stack(
        [torch.cat([i, i.new_zeros((i.shape[0], max_len - i.shape[1]))], 1) for i in masks], 0)

    if max_len > cut_limit:
        padded_video = padded_video[:, :, :cut_limit, ...]
        padded_labels = padded_labels[:, :, :cut_limit, ...]
        padded_masks = padded_masks[:, :, :cut_limit, ...]
    # print(padded_audio.shape, padded_video.shape, padded_labels.shape, padded_masks.shape)
    return padded_audio, padded_video, padded_labels, padded_masks


def main():
    # The structure of this code is learnt from https://github.com/clovaai/voxceleb_trainer
    warnings.filterwarnings("ignore")

    parser = argparse.ArgumentParser(description="TalkNet Training")
    # Training details
    parser.add_argument('--lr', type=float, default=0.0001, help='Learning rate')
    parser.add_argument('--lrDecay', type=float, default=0.95, help='Learning rate decay rate')
    parser.add_argument('--maxEpoch', type=int, default=25, help='Maximum number of epochs')
    parser.add_argument('--testInterval',
                        type=int,
                        default=1,
                        help='Test and save every [testInterval] epochs')
    parser.add_argument(
        '--batchSize',
        type=int,
        default=2500,
        help=
        'Dynamic batch size, default is 2500 frames, other batchsize (such as 1500) will not affect the performance'
    )
    parser.add_argument('--batch_size', type=int, default=1, help='batch_size')
    parser.add_argument('--num_speakers', type=int, default=5, help='num_speakers')
    parser.add_argument('--nDataLoaderThread', type=int, default=4, help='Number of loader threads')
    # Data path
    parser.add_argument('--dataPathAVA',
                        type=str,
                        default="/data08/AVA",
                        help='Save path of AVA dataset')
    parser.add_argument('--savePath', type=str, default="exps/exp1")
    # Data selection
    parser.add_argument('--evalDataType',
                        type=str,
                        default="val",
                        help='Only for AVA, to choose the dataset for evaluation, val or test')
    # For download dataset only, for evaluation only
    parser.add_argument('--downloadAVA',
                        dest='downloadAVA',
                        action='store_true',
                        help='Only download AVA dataset and do related preprocess')
    parser.add_argument('--evaluation',
                        dest='evaluation',
                        action='store_true',
                        help='Only do evaluation by using pretrained model [pretrain_AVA.model]')
    args = parser.parse_args()
    # Data loader
    args = init_args(args)

    if args.downloadAVA == True:
        preprocess_AVA(args)
        quit()

    loader = train_loader(trialFileName = args.trainTrialAVA, \
                          audioPath      = os.path.join(args.audioPathAVA , 'train'), \
                          visualPath     = os.path.join(args.visualPathAVA, 'train'), \
                          # num_speakers = args.num_speakers, \
                          **vars(args))
    trainLoader = torch.utils.data.DataLoader(loader,
                                              batch_size=args.batch_size,
                                              shuffle=True,
                                              num_workers=args.nDataLoaderThread,
                                              collate_fn=collate_fn_padding)

    loader = val_loader(trialFileName = args.evalTrialAVA, \
                        audioPath     = os.path.join(args.audioPathAVA , args.evalDataType), \
                        visualPath    = os.path.join(args.visualPathAVA, args.evalDataType), \
                        # num_speakers = args.num_speakers, \
                        **vars(args))
    valLoader = torch.utils.data.DataLoader(loader, batch_size=1, shuffle=False, num_workers=16)

    if args.evaluation == True:
        download_pretrain_model_AVA()
        s = talkNet(**vars(args))
        s.loadParameters('pretrain_AVA.model')
        print("Model %s loaded from previous state!" % ('pretrain_AVA.model'))
        mAP = s.evaluate_network(loader=valLoader, **vars(args))
        print("mAP %2.2f%%" % (mAP))
        quit()

    modelfiles = glob.glob('%s/model_0*.model' % args.modelSavePath)
    modelfiles.sort()
    if len(modelfiles) >= 1:
        print("Model %s loaded from previous state!" % modelfiles[-1])
        epoch = int(os.path.splitext(os.path.basename(modelfiles[-1]))[0][6:]) + 1
        s = talkNet(epoch=epoch, **vars(args))
        s.loadParameters(modelfiles[-1])
    else:
        epoch = 1
        s = talkNet(epoch=epoch, **vars(args))

    mAPs = []
    scoreFile = open(args.scoreSavePath, "a+")

    while (1):
        loss, lr = s.train_network(epoch=epoch, loader=trainLoader, **vars(args))

        if epoch % args.testInterval == 0:
            s.saveParameters(args.modelSavePath + "/model_%04d.model" % epoch)
            mAPs.append(s.evaluate_network(epoch=epoch, loader=valLoader, **vars(args)))
            print(time.strftime("%Y-%m-%d %H:%M:%S"),
                  "%d epoch, mAP %2.2f%%, bestmAP %2.2f%%" % (epoch, mAPs[-1], max(mAPs)))
            scoreFile.write("%d epoch, LR %f, LOSS %f, mAP %2.2f%%, bestmAP %2.2f%%\n" %
                            (epoch, lr, loss, mAPs[-1], max(mAPs)))
            scoreFile.flush()

        if epoch >= args.maxEpoch:
            quit()

        epoch += 1


if __name__ == '__main__':
    main()
