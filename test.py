import argparse
import sys
import os
import time


from absl import logging
from absl import app


import numpy as np
import tensorflow as tf


from libs.predict_net import PredictNet
from libs.utils import get_learning_rate_scheduler
from libs.dataset import get_dataset
from libs.utils import set_cuda_visible_device

FLAGS = None
np.set_printoptions(3)
tf.random.set_seed(1234)
cmd = set_cuda_visible_device(1)
print ("Using ", cmd[:-1], "-th GPU")
os.environ["CUDA_VISIBLE_DEVICES"] = cmd[:-1]


def save_outputs(model, dataset, metrics, model_name, mc_dropout=False):
    
    label_total = np.empty([0,])
    pred_total = np.empty([0,])

    st = time.time()
    for batch, (x, adj, label) in enumerate(dataset):

        pred = None
        if mc_dropout:
            pred = [model(x, adj, True) for _ in range(FLAGS.mc_sampling)]
            pred = tf.reduce_mean(pred, axis=0)
        else:    
            pred = model(x, adj, False)

        label_total = np.concatenate((label_total, label.numpy()), axis=0)
        pred_total = np.concatenate((pred_total, pred.numpy()), axis=0)
        for metric in metrics:
            metric(label, pred)

    et = time.time()

    print ("Test accuracy:", metrics[0].result().numpy(), \
           "AUROC:", metrics[1].result().numpy(), \
           "AUPRC:", metrics[2].result().numpy(), \
           "Precision:", metrics[3].result().numpy(), \
           "Recall", metrics[4].result().numpy(), \
           "MC-dropout:", str(mc_dropout),
           "Time:", round(et-st,3))

    for metric in metrics:
        metric.reset_states()
        
    model_name += '_' + str(mc_dropout)
    np.save('./outputs/'+model_name+'_label.npy', label_total)
    np.save('./outputs/'+model_name+'_pred.npy', pred_total)
    
    return


def evaluation_step(model, dataset, metrics, mc_dropout=False):

    st = time.time()
    for (batch, (x, adj, label)) in enumerate(dataset):

        pred = None
        if mc_dropout:
            pred = [model(x, adj, True) for _ in range(FLAGS.mc_sampling)]
            pred = tf.reduce_mean(pred, axis=0)
        else:    
            pred = model(x, adj, False)

        for metric in metrics:
            metric(label, pred)
    et = time.time()

    print ("Test accuracy:", metrics[0].result().numpy(), \
           "AUROC:", metrics[1].result().numpy(), \
           "AUPRC:", metrics[2].result().numpy(), \
           "Precision:", metrics[3].result().numpy(), \
           "Recall", metrics[4].result().numpy(), \
           "Time:", round(et-st,3))

    for metric in metrics:
        metric.reset_states()

    return


def train_step(model, optimizer, loss_fn, dataset, metrics):
    
    st = time.time()
    for (batch, (x, adj, label)) in enumerate(dataset):
        with tf.GradientTape() as tape:
            pred = model(x, adj, True)
            loss = loss_fn(label, pred)
            loss += 2.0*FLAGS.weight_decay*tf.reduce_sum(
                [tf.nn.l2_loss(w) for w in model.trainable_weights]
            )    
        grads = tape.gradient(loss, model.trainable_variables)                
        optimizer.apply_gradients(zip(grads, model.trainable_variables))
        for metric in metrics:
            metric(label, pred)
    et = time.time()

    print ("Train accuracy:", metrics[0].result().numpy(), \
           "AUROC:", metrics[1].result().numpy(), \
           "AUPRC:", metrics[2].result().numpy(), \
           "Precision:", metrics[3].result().numpy(), \
           "Recall:", metrics[4].result().numpy(), \
           "Time:", round(et-st,3))

    for metric in metrics:
        metric.reset_states()

    return


def test(model, train_ds, test_ds):

    model_name = FLAGS.prefix
    model_name += '_' + FLAGS.prop
    model_name += '_' + str(FLAGS.seed)
    model_name += '_' + str(FLAGS.num_layers)
    model_name += '_' + str(FLAGS.node_dim)
    model_name += '_' + str(FLAGS.graph_dim)
    model_name += '_' + str(FLAGS.use_attn)
    model_name += '_' + str(FLAGS.num_heads)
    model_name += '_' + str(FLAGS.use_ln)
    model_name += '_' + str(FLAGS.use_ffnn)
    model_name += '_' + str(FLAGS.dropout_rate)
    model_name += '_' + str(FLAGS.weight_decay)
    model_name += '_' + str(FLAGS.readout_method)
    model_name += '_' + str(FLAGS.concat_readout)
    ckpt_path = './save/'+model_name

    scheduler = get_learning_rate_scheduler(
        lr_schedule=FLAGS.lr_schedule, 
        graph_dim=FLAGS.graph_dim, 
        warmup_steps=FLAGS.warmup_steps)

    optimizer = tf.keras.optimizers.Adam(
        learning_rate=scheduler,
        beta_1=FLAGS.beta_1,
        beta_2=FLAGS.beta_2,
        epsilon=FLAGS.opt_epsilon
    )    

    checkpoint = tf.train.Checkpoint(
        model=model, 
        optimizer=optimizer
    )

    ckpt_manager = tf.train.CheckpointManager(
        checkpoint=checkpoint, 
        directory=ckpt_path, 
        max_to_keep=FLAGS.max_to_keep
    )

    test_metrics = [
        tf.keras.metrics.BinaryAccuracy(name='Test_Accuracy'),
        tf.keras.metrics.AUC(curve='ROC', name='Test_AAUROC'),
        tf.keras.metrics.AUC(curve='PR', name='Test_AUPRC'),
        tf.keras.metrics.Precision(name='Test_Precision'),
        tf.keras.metrics.Recall(name='Test_Recall'),
    ]

    status = checkpoint.restore(ckpt_manager.latest_checkpoint)

    save_outputs(model, test_ds, test_metrics, model_name, False)
    save_outputs(model, test_ds, test_metrics, model_name, True)

    return


def main(_):

    def print_model_spec():
        print ("Target property", FLAGS.prop)
        print ("Random seed for data spliting", FLAGS.seed)
        print ("Number of graph convoltuion layers", FLAGS.num_layers)
        print ("Dimensionality of node features", FLAGS.node_dim)
        print ("Dimensionality of graph features", FLAGS.graph_dim)
        print ()
        print ("Whether to use attentions in node embeddings", \
                                                     FLAGS.use_attn)
        print ("Number of attention heads", FLAGS.num_heads)
        print ("Whether to use layer normalization", FLAGS.use_ln)
        print ("Whether to use feed-forward network", FLAGS.use_ffnn)
        print ("Dropout rate", FLAGS.dropout_rate)
        print ("Dropout rate", FLAGS.weight_decay)
        print ()
        print ("Readout method", FLAGS.readout_method)
        print ("Pooling operation", FLAGS.pooling)
        print ()
        print ("Learning rate scheduling", FLAGS.lr_schedule)
        return
    
    model = PredictNet(
        num_layers=FLAGS.num_layers,
        node_dim=FLAGS.node_dim,
        graph_dim=FLAGS.graph_dim,
        use_attn=FLAGS.use_attn,
        num_heads=FLAGS.num_heads,
        use_ln=FLAGS.use_ln,
        use_ffnn=FLAGS.use_ffnn,
        dropout_rate=FLAGS.dropout_rate,
        readout_method=FLAGS.readout_method,
        concat_readout=FLAGS.concat_readout
    )

    train_ds, test_ds, num_total, num_train = get_dataset(
        #prop=FLAGS.prop, 
        prop=FLAGS.prop+'_chembl', 
        batch_size=FLAGS.batch_size,
        train_ratio=0.0,
        seed=FLAGS.seed
    )

    print_model_spec()
    print ("Number of training and test data:", num_train, num_total-num_train)

    test(model, train_ds, test_ds)
    return

if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    def str2bool(v):
        if v.lower() in ('yes', 'true', 't', 'y', '1'):
            return True
        elif v.lower() in ('no', 'false', 'f', 'n', '0'):
            return False    
        else:
            raise argparse.ArgumentTypeEror('Boolean value expected')    

    # Hyper-parameters for prefix, prop and random seed
    parser.add_argument('--prefix', type=str, default='GTA', 
                        help='Prefix for this training')
    parser.add_argument('--prop', type=str, default='bace_c', 
                        help='Target property to train')
    parser.add_argument('--seed', type=int, default=1111, 
                        help='Random seed will be used to shuffle dataset')

    # Hyper-parameters for model construction
    parser.add_argument('--num_layers', type=int, default=4, 
                        help='Number of node embedding layers')
    parser.add_argument('--node_dim', type=int, default=64, 
                        help='Dimension of node embeddings')
    parser.add_argument('--graph_dim', type=int, default=256, 
                        help='Dimension of a graph embedding')
    parser.add_argument("--use_attn", type=str2bool, default=False, 
                        help='Whether to use multi-head attentions')
    parser.add_argument('--num_heads', type=int, default=4, 
                        help='Number of attention heads')
    parser.add_argument("--use_ln", type=str2bool, default=True, 
                        help='Whether to use layer normalizations')
    parser.add_argument('--use_ffnn', type=str2bool, default=False, 
                        help='Whether to use feed-forward nets')
    parser.add_argument('--dropout_rate', type=float, default=0.2, 
                        help='Dropout rates in node embedding layers')
    parser.add_argument('--weight_decay', type=float, default=1e-6, 
                        help='Weight decay coefficient')
    parser.add_argument('--readout_method', type=str, default='pma', 
                        help='Readout method to be used')
    parser.add_argument('--concat_readout', type=str2bool, default=True, 
                        help='Whether to concatenate readout vectors')
    parser.add_argument('--pooling', type=str, default='mean', 
                        help='Pooling operations in readouts, \
                             Options: mean, sum, max')


    # Hyper-parameaters for loss function
    parser.add_argument('--loss_type', type=str, default='bce', 
                        help='Loss function will be used, \
                             Options: bce, focal, class_balanced, max_margin')



    # Hyper-parameters for training
    parser.add_argument('--batch_size', type=int, default=128, 
                        help='Batch size')
    parser.add_argument('--num_epoches', type=int, default=200, 
                        help='Number of epoches')
    parser.add_argument('--lr_schedule', type=str, default='stair', 
                        help='How to schedule learning rate')
    parser.add_argument('--init_lr', type=float, default=1e-3, 
                        help='Initial learning rate,\
                              Do not need for warmup scheduling')
    parser.add_argument('--beta_1', type=float, default=0.9, 
                        help='Beta1 in adam optimizer')
    parser.add_argument('--beta_2', type=float, default=0.999, 
                        help='Beta2 in adam optimizer')
    parser.add_argument('--opt_epsilon', type=float, default=1e-7, 
                        help='Epsilon in adam optimizer')
    parser.add_argument('--warmup_steps', type=int, default=2000, 
                        help='Warmup steps for warmup scheduling')
    parser.add_argument('--decay_steps', type=int, default=1000, 
                        help='Decay steps for stair learning rate scheduling')
    parser.add_argument('--decay_rate', type=float, default=0.1, 
                        help='Decay rate for stair learning rate scheduling')
    parser.add_argument('--max_to_keep', type=int, default=5, 
                        help='Maximum number of checkpoint files to be kept')
    parser.add_argument("--save_model", type=str2bool, default=False, 
                        help='Whether to save checkpoints')


    # Hyper-parameters for evaluation
    parser.add_argument("--save_outputs", type=str2bool, default=True, 
                        help='Whether to save final predictions for test dataset')
    parser.add_argument('--mc_dropout', type=str2bool, default=False, 
                        help='Whether to infer predictive distributions with MC-dropout')
    parser.add_argument('--mc_sampling', type=int, default=50,
                       help='Number of MC sampling')
    parser.add_argument('--top_k', type=int, default=50,
                       help='Top-k instances for evaluating Precision or Recall')

    FLAGS, unparsed = parser.parse_known_args()
    app.run(main=main, argv=[sys.argv[0]] + unparsed)

