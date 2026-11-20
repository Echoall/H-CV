"""UART1 pin mapping for the Hiwonder CanMV K230."""

from machine import FPIOA, UART


TX_IO = 3
RX_IO = 4


class UART1Link:
    def __init__(self, baud):
        self.mux = FPIOA()
        self.previous_tx = self.mux.get_pin_func(TX_IO)
        self.previous_rx = self.mux.get_pin_func(RX_IO)
        self.uart = None
        try:
            self.mux.set_function(TX_IO, FPIOA.UART1_TXD)
            self.mux.set_function(RX_IO, FPIOA.UART1_RXD)
            self.uart = UART(UART.UART1, baud, bits=8, stop=1, timeout=2)
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.uart is not None:
                self.uart.deinit()
        finally:
            try:
                self.mux.set_function(TX_IO, self.previous_tx)
            finally:
                self.mux.set_function(RX_IO, self.previous_rx)
