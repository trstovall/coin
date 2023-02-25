
from functools import cached_property
from struct import pack, unpack

from .crypto import keccak_800, keccak_1600, sign, verify


class Parameters(object):

    def __init__(self,
        target: bytes,          # compact difficulty to mint a block
        block_reward: int,      # units generated each block link
        stamp_reward: int,      # units generated by workstamps for block target
        data_fee: int,          # units to destroy per byte in payment
        expiry: int             # forget UTXOs and workstamps older than expiry
    ):
        self.target = target
        self.block_reward = block_reward
        self.stamp_reward = stamp_reward
        self.data_fee = data_fee
        self.expiry = expiry
    
    @classmethod
    def from_bytes(cls, data: bytes) -> "Parameters":
        if len(data) < 6:
            raise Exception("Not enough data to unpack Parameters.")
        target = data[:2]
        a, b, c, d = data[2:6]
        if len(data) != 6 + sum([a, b, c, d]):
            raise Exception("Cannot unpack Parameters from data.")
        s, e = 6, 6 + a
        block_reward = sum(x << (8*i) for i,x in enumerate(data[s:e]))
        s, e = e, e + b
        stamp_reward = sum(x << (8*i) for i,x in enumerate(data[s:e]))
        s, e = e, e + c
        data_fee = sum(x << (8*i) for i,x in enumerate(data[s:e]))
        s, e = e, e + d
        expiry = sum(x << (8*i) for i,x in enumerate(data[s:e]))
        return cls(target, block_reward, stamp_reward, data_fee, expiry)

    def to_bytes(self) -> bytes:
        a = (self.block_reward.bit_length() + 7) // 8
        b = (self.stamp_reward.bit_length() + 7) // 8
        c = (self.data_fee.bit_length() + 7) // 8
        d = (self.expiry.bit_length() + 7) // 8
        return (self.target
            + bytes(
                [a, b, c, d]
                + [(self.block_reward >> (8*i)) & 0xff for i in range(a)]
                + [(self.stamp_reward >> (8*i)) & 0xff for i in range(b)]
                + [(self.data_fee >> (8*i)) & 0xff for i in range(c)]
                + [(self.expiry >> (8*i)) & 0xff for i in range(d)]
            )
        )


class WorkBlock(object):

    def __init__(self,
        timestamp: int,                 # seconds since UNIX epoch
        prev_block: bytes,              # hash digest of most recent block
        prev_work_block: bytes,         # hash digest of most recent work block
        worker: bytes | None = None,    # ed25519 key of block worker
        nonce: bytes | None = None,     # nonce required to hash block to target difficulty
        parameters: Parameters | None = None
    ):
        self.timestamp = timestamp
        self.prev_block = prev_block
        self.prev_work_block = prev_work_block
        self.worker = worker
        self.nonce = nonce
        self.parameters = parameters

    @cached_property
    def index_bytes(self) -> bytes:
        len = (self.index.bit_length() + 7) // 8
        return bytes([len] + [
            self.index >> (8*i) for i in range(len)
        ])

    @cached_property
    def timestamp_bytes(self) -> bytes:
        len = (self.timestamp.bit_length() + 7) // 8
        return bytes([len] + [
            self.timestamp >> (8*i) for i in range(len)
        ])
    
    @cached_property
    def total_work_bytes(self) -> bytes:
        len = (self.total_work.bit_length() + 7) // 8
        return bytes([len] + [
            self.total_work >> (8*i) for i in range(len)
        ])

    @cached_property
    def work_key(self) -> bytes:
        if self.worker is None:
            raise Exception("No worker set for block.")
        data = b''.join([
            self.index_bytes,
            self.prev_block,
            self.prev_link,
            self.timestamp_bytes,
            self.total_work_bytes,
            self.worker
        ])
        return keccak_1600(data)
    
    def mint(self, limit: int = 1000, nonce: bytes | None = None) -> bytes | None:
        key = self.work_key
        target = self.parameters.target
        nonce = nonce or self.nonce or (b'\x00' * 32)
        base, exp = target
        len = (exp + 7) // 8
        for iteration in range(limit):
            digest = keccak_800(b''.join(key, target, nonce))
            if base >= digest[0]:
                exp_sum = sum(digest[i+1] << (8*i) for i in range(len))
                if not exp_sum % (1 << exp):
                    self.nonce = nonce
                    return digest
            new_nonce = sum(x << (8*i) for i,x in enumerate(nonce)) + 1
            nonce = bytes([
                (new_nonce >> (8*i)) % 0xff for i in range(len(nonce))
            ])
        self.nonce = nonce
        return None

    @property
    def digest(self) -> bytes | None:
        if not self.worker or not self.nonce:
            return None
        data = b''.join(
            self.work_key,
            self.parameters.target,
            self.nonce
        )
        digest = keccak_800(data)
        base, exp = self.parameters.target
        if base >= digest[0]:
            exp_sum = sum(digest[i+1] << (8*i) for i in range((exp + 7) // 8))
            if not exp_sum % (1 << exp):
                return digest
        return None


class PreviousPaymentOutput(object):

    def __init__(self,
        digest: bytes,      # block digest or H(payment link | payment index | output index)
        key: bytes,         # ed25519 public key that hashes to output address
    ):
        self.digest = digest
        self.key = key
    
    def spend(self, seed: bytes, new_outputs_digest: bytes) -> "OutputSpend":
        signature = sign(seed, new_outputs_digest)[:64]
        return OutputSpend(self, signature)


class OutputSpend(object):

    def __init__(self,
        output: PreviousPaymentOutput,
        signature: bytes        # 64-byte ed25519 signature signing Payment outputs
    ):
        self.output = output
        self.signature = signature

    def signed(self, new_outputs_digest: bytes) -> bool:
        return verify(self.output.key, self.signature + new_outputs_digest)


class Workstamp(object):

    def __init__(self,
        key: bytes,             # ed25519 public key used to spend workstamp
        target: bytes,          # 2-byte float from 0 to 2**256-1
        nonce: bytes | None     # 1-32 bytes such that H(key | target | nonce)
    ):
        self.key = key
        self.target = target
        self.nonce = nonce
    
    def spend(self, seed: bytes, new_outputs_digest: bytes) -> "WorkstampSpend":
        signature = sign(seed, new_outputs_digest)[:64]
        return WorkstampSpend(self, signature)

    def mint(self, limit: int = 1000, nonce: bytes | None = None) -> bytes | None:
        key = self.key
        target = self.target
        nonce = nonce or self.nonce or (b'\x00' * 32)
        base, exp = target
        len = (exp + 7) // 8
        for iteration in range(limit):
            digest = keccak_800(b''.join(key, target, nonce))
            if base >= digest[0]:
                exp_sum = sum(digest[i+1] << (8*i) for i in range(len))
                if not exp_sum % (1 << exp):
                    self.nonce = nonce
                    return digest
            new_nonce = sum(x << (8*i) for i,x in enumerate(nonce)) + 1
            nonce = bytes([
                (new_nonce >> (8*i)) % 0xff for i in range(len(nonce))
            ])
        self.nonce = nonce

    @property
    def digest(self) -> bytes | None:
        if not self.nonce:
            return None
        data = b''.join(
            self.key,
            self.target,
            self.nonce
        )
        digest = keccak_800(data)
        base, exp = self.target
        if base >= digest[0]:
            exp_sum = sum(digest[i+1] << (8*i) for i in range((exp + 7) // 8))
            if not exp_sum % (1 << exp):
                return digest
        return None


class WorkstampSpend(object):

    def __init__(self,
        workstamp: Workstamp,
        signature: bytes        # 64-byte ed25519 signature signing Payment outputs
    ):
        self.workstamp = workstamp
        self.signature = signature

    def signed(self, new_outputs_digest: bytes) -> bool:
        return verify(self.workstamp.key, self.signature + new_outputs_digest)

    def balance(self, parameters: Parameters) -> int:
        if self.workstamp.digest:
            base, exp = self.workstamp.target
            stamp_target = (base + 1) * 2 ** exp - 1
            base, exp = parameters.target
            target = (base + 1) * 2 ** exp - 1
            return (parameters.block_reward * stamp_target) // target


class Vote(object):

    def __init__(self,
        block_reward: int = 0,
        stamp_reward: int = 0,
        data_fee: int = 0,
        block_expiry: int = 0
    ):
        self.block_reward = block_reward
        self.stamp_reward = stamp_reward
        self.data_fee = data_fee
        self.block_expiry = block_expiry


class PaymentOutput(object):

    def __init__(self,
        address: bytes | None,      # 0-32 bytes digest of receipient's public key
        units: int = 0,             # 1 coin = 2**32 units
        memo: bytes | None = None,  # raw data to add to blockchain
        vote: Vote | None = None    # adjustments to blockchain parameters
    ):
        self.address = address
        self.units = units
        self.memo = memo
        self.vote = vote


class Payment(object):

    def __init__(self,
        inputs: list[OutputSpend | WorkstampSpend] = [],
        outputs: list[PaymentOutput] = [],
        content_length: int | None = None
    ):
        self.inputs = inputs
        self.outputs = outputs
        self.content_length = content_length

    def to_bytes(self):
        pass


class PaymentLink(object):

    def __init__(self,
        index: int,         # links since block link
        prev_block: bytes,  # keccak800 digest of most recent block
        prev_link: bytes,   # keccak800 digest of most recent link
        timestamp: int,     # seconds since epoch, unique
        payments: list[Payment] = [],
        signature: bytes | None = None  # 64-byte ed25519 signature of link digest
    ):
        self.index = index,
        self.prev_block = prev_block
        self.prev_link = prev_link
        self.timestamp = timestamp
        self.payments = payments
        self.signature = signature
