# How the three deployed models were chosen

All 9 candidates were trained and evaluated on the same block-wise test split. The model with the best macro F1 in each family was kept.

| Model | Family | Accuracy | Macro F1 | False-alarm rate | Leak recall | Train [s] | Size [KB] | Latency [ms] | Kept |
|---|---|---|---|---|---|---|---|---|---|
| logistic_regression | Traditional ML | 0.9667 | 0.8992 | 0.0311 | 0.937 | 0.26 | 3 | 7.02 |  |
| knn | Traditional ML | 0.9889 | 0.9700 | 0.0034 | 0.923 | 0.02 | 3789 | 7.62 | **yes** |
| decision_tree | Traditional ML | 0.9642 | 0.8994 | 0.0283 | 0.866 | 0.29 | 10 | 5.93 |  |
| naive_bayes | Traditional ML | 0.3471 | 0.4989 | 0.7509 | 0.858 | 0.02 | 4 | 6.32 |  |
| random_forest | Ensemble ML | 0.9967 | 0.9872 | 0.0008 | 0.987 | 0.59 | 2484 | 14.19 |  |
| extra_trees | Ensemble ML | 0.9987 | 0.9962 | 0.0006 | 0.991 | 0.24 | 5679 | 14.33 | **yes** |
| gradient_boosting | Ensemble ML | 0.9962 | 0.9848 | 0.0022 | 0.989 | 4.27 | 1094 | 10.61 |  |
| mlp | Deep Learning | 0.9836 | 0.9452 | 0.0137 | 0.945 | 5.55 | 64 | 6.13 |  |
| cnn_lstm | Deep Learning | 0.9871 | 0.9500 | 0.0138 | 0.992 | 10.63 | 109 | 0.77 | **yes** |
