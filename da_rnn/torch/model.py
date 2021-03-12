import torch
from torch.nn import (
    Module,
    Linear,
    LSTM
)


DEVICE = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')


class Encoder(Module):
    n: int
    T: int
    m: int

    def __init__(
        self,
        n,
        T,
        m,
        dropout=0
    ):
        """
        Generates the new input X_tilde for encoder

        Args:
            n (int): input size, the number of features of a single driving series
            T (int): the size (time steps) of the window
            m (int): the number of the encoder hidden states
        """

        super().__init__()

        self.n = n
        self.T = T
        self.m = m

        self.dropout = dropout

        # Two linear layers forms a bigger linear layer
        self.WU_e = Linear(m * 2 + T, T, False)

        # Since v_e ∈ R^T, the input size is T
        self.v_e = Linear(T, 1, False)

        self.lstm = LSTM(self.n, self.m, dropout=self.dropout)

    def forward(self, X):
        """
        Args:
            X: the n driving (exogenous) series of shape (batch_size, T, n)

        Returns:
            The encoder hidden state of shape (T, batch_size, m)
        """

        batch_size = X.shape[0]

        hidden_state = torch.zeros(1, batch_size, self.m, device=DEVICE)
        cell_state = torch.zeros(1, batch_size, self.m, device=DEVICE)

        X_encoded = torch.zeros(self.T, batch_size, self.m, device=DEVICE)

        for t in range(self.T):
            # [h_t-1; s_t-1]
            hs = torch.cat((hidden_state, cell_state), 2)
            # -> (1, batch_size, m * 2)

            hs = hs.permute(1, 0, 2).repeat(1, self.n, 1)
            # -> (batch_size, n, m * 2)

            tanh = torch.tanh(
                self.linear(
                    torch.cat((hs, X.permute(0, 2, 1)), 2)
                    # -> (batch_size, n, m * 2 + T)
                )
            )
            # -> (batch_size, n, T)

            # Equation 8
            E = self.v_e(tanh).view(batch_size, self.n)
            # -> (batch_size, n)

            # Equation 9
            Alpha_t = torch.softmax(E, 1)
            # -> (batch_size, n)

            # Ref
            # https://pytorch.org/docs/stable/generated/torch.nn.LSTM.html
            # The input shape of torch LSTM should be
            # (seq_len, batch, n)
            _, (hidden_state, cell_state) = self.lstm(
                (X[:, t, :] * Alpha_t).unsqueeze(0),
                # -> (1, batch_size, n)
                (hidden_state, cell_state)
            )

            X_encoded[t] = hidden_state[0]

        return X_encoded


class Decoder(Module):
    n: int
    T: int
    m: int
    p: int
    y_dim: int

    def __init__(
        self,
        n,
        T,
        m,
        p,
        y_dim,
        dropout
    ):
        """
        Calculates y_hat_T

        Args:
            T (int): the size (time steps) of the window
            m (int): the number of the encoder hidden states
            p (int): the number of the decoder hidden states
            y_dim (int): prediction dimentionality
        """

        super().__init__()

        self.n = n
        self.T = T
        self.m = m
        self.p = p
        self.y_dim = y_dim
        self.dropout = dropout

        self.WU_d = Linear(p * 2 + m, m, False)
        self.v_d = Linear(m, 1, False)
        self.linear = Linear(y_dim + m, 1, False)

        self.lstm = LSTM(1, p, dropout=self.dropout)

        self.Wb = Linear(p + m, p)
        self.vb = Linear(p, y_dim)

    def forward(self, Y, X_encoded):
        """
        Args:
            Y: prediction data of shape (batch_size, T - 1, y_dim) from time 1 to time T - 1. See Figure 1(b) in the paper
            X_encoded: encoder hidden states of shape (T, batch_size, m)

        Returns:
            y_hat_T: the prediction of shape (batch_size, 1, y_dim)
        """

        batch_size = Y.shape[0]

        hidden_state = torch.zeros(1, batch_size, self.p, device=DEVICE)
        cell_state = torch.zeros(1, batch_size, self.p, device=DEVICE)

        for t in range(self.T - 1):
            # Equation 12
            l = self.v_d(
                torch.tanh(
                    self.WU_d(
                        torch.cat(
                            (
                                torch.cat(
                                    (hidden_state, cell_state),
                                    2
                                ).permute(1, 0, 2).repeat(1, self.T, 1),
                                # -> (batch_size, T, p * 2)

                                X_encoded.permute(1, 0, 2)
                                # -> (batch_size, T, m)
                            ),
                            2
                        )
                    )
                    # -> (batch_size, T, m * 2)
                )
                # -> (batch_size, T, m)
            ).view(batch_size, self.T)
            # -> (batch_size, T)

            # Equation 13
            Beta_t = torch.softmax(l, 1)
            # -> (batch_size, T)

            # Equation 14
            context_vector = torch.bmm(
                Beta_t.unsqueeze(1),
                # -> (batch_size, 1, T)
                X_encoded.permute(1, 0, 2)
                # -> (batch_size, T, m)
            ).squeeze(1)
            # -> (batch_size, m)

            # Equation 15
            y_tilde = self.linear(
                torch.cat((Y[:, t, :], context_vector), 1)
                # -> (batch_size, y_dim + m)
            )
            # -> (batch_size, 1)

            # Equation 16
            _, (hidden_state, cell_state) = self.lstm(
                y_tilde.unsqueeze(0),
                # -> (1, batch_size, 1)
                (hidden_state, cell_state)
            )

        # Equation 22
        y_hat_T = self.vb(
            self.Wb(
                torch.cat((hidden_state.squeeze(0), context_vector), 1)
                # -> (batch_size, p + m)
            )
            # -> (batch_size, p)
        )
        # -> (batch_size, 1)

        return y_hat_T