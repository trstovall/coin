
from functools import cached_property
from struct import pack, unpack

from .crypto import keccak_800, keccak_1600, sign, verify



class ParametersBuilder(object):

    def __init__(self,
        target: int,            # mint difficulty x * 2 ** y
        block_reward: int,      # units generated each block
        utxo_fee: int,          # decay of UTXOs per block as a fraction z / 2**64
        data_fee: int,          # units to destroy per byte in payment
    ):
        self.target = target
        self.block_reward = block_reward
        self.utxo_fee = utxo_fee
        self.data_fee = data_fee

    def build(self) -> bytes:
        y = max(0, self.target.bit_length() - 8)
        x = self.target >> y
        return bytes(
            [x, y]
            + [(self.block_reward >> (8*i)) & 0xff for i in range(8)]
            + [(self.utxo_fee >> (8*i)) & 0xff for i in range(8)]
            + [(self.data_fee >> (8*i)) & 0xff for i in range(8)]
        )


class ParametersView(object):

    def __init__(self, view: memoryview):
        if len(view) != 26:
            raise ValueError("Cannot unpack Parameters from view.")
        self.view = view

    @property
    def target(self) -> int:
        return self.view[0] * (1 << self.view[1])

    @property
    def block_reward(self) -> int:
        return sum(x << (8*i) for i,x in enumerate(self.view[2:10]))
    
    @property
    def utxo_fee(self) -> int:
        return sum(x << (8*i) for i,x in enumerate(self.view[10:18]))
    
    @property
    def data_fee(self) -> int:
        return sum(x << (8*i) for i,x in enumerate(self.view[18:26]))


class UID(object):

    PUBLIC_KEY = 0
    TRUNCATED_HASH = 1

    @property
    def buffer_size(self) -> int:
        return 1 + (len(self.hash) if self.hash is not None else 32)

    def __init__(self, key: bytes = None, hash: bytes = None):
        if key is None and hash is None:
            raise ValueError('UID must be instantiated from either a key or a hash')
        self.key = key
        self.hash = hash

    def write_into(self, dest: memoryview) -> memoryview:
        if self.hash:
            l = len(self.hash)
            dest[0] = (l << 1) | 1
            dest[1:1+l] = self.hash
        else:
            l = 32
            dest[0] = 0
            dest[1:1+l] = self.key
        return dest[:1+l]

    @classmethod
    def from_view(cls, view: memoryview) -> "UID":
        x = view[0]
        l = (x << 1) if x & 1 else 32
        if x < 16 or x > 32:
            raise ValueError(f'Truncated hash cannot be of length {x}')
        match x & 1:
            case cls.PUBLIC_KEY:
                k = 'key'
            case cls.TRUNCATED_HASH:
                k = 'hash'
        return cls(**{k: bytes(view[1:1+l])})

    @classmethod
    def hash_key_into(cls, dest: memoryview, key: bytes, truncate: int = 16) -> memoryview:
        dest[0] = (truncate << 1) | cls.TRUNCATED_HASH
        dest[1:1+truncate] = keccak_800(key)[:truncate]
        return dest[0:1+truncate]


SPENDER_LIST = 0
SPENDER_HASH = 1
SPENDER_KEY = 2


class SpenderHash(object):

    def __init__(self, hash: bytes):
        self.hash = hash

    def encode(self) -> bytes:
        return bytes([(len(self.hash) << 2) | SPENDER_HASH]) + self.hash


class SpenderKey(object):

    def __init__(self, key: bytes, truncate: int = 0):
        self.key = key
        self.truncate = truncate

    def hash(self) -> SpenderHash:
        return SpenderHash(
            keccak_800(self.key)[:self.truncate]
            if self.truncate else
            self.key
        )
    
    def encode(self) -> bytes:
        return bytes([(self.truncate << 2) | SPENDER_KEY]) + self.key


class SpenderList(object):

    def __init__(self, spenders: list["SpenderList" | SpenderHash | SpenderKey],
            threshold: int, truncate: int = 16):
        self.spenders = spenders
        self.threshold = threshold
        self.truncate = truncate

    def hash(self) -> SpenderHash:
        def _hash(spender: "SpenderList" | SpenderHash | SpenderKey) -> SpenderHash:
            return spender if type(spender) == SpenderHash else spender.hash()
        if not self.spenders or not self.threshold:
            raise ValueError("Cannot hash empty SpenderList.")
        if len(self.spenders) == 1:
            return _hash(self.spenders[0])
        prefix = [(self.truncate << 2) | SPENDER_LIST]
        if len(self.spenders) < 128:
            prefix.append(len(self.spenders) << 1)
        else:
            prefix.append(((len(self.spenders) & 0x7f) << 1) | 1)
            prefix.append((len(self.spenders) >> 7) & 0xff)
        if self.threshold < 128:
            prefix.append(self.threshold << 1)
        else:
            prefix.append(((self.threshold & 0x7f) << 1) | 1)
            prefix.append((self.threshold >> 7) & 0xff)
        prefix = bytes(prefix)
        spenders = [_hash(s).hash for s in self.spenders]
        buffer = bytearray(len(prefix) + sum(len(s) for s in spenders))
        view = memoryview(buffer)
        i = 0
        view[i:i+len(prefix)] = prefix
        i += len(prefix)
        for s in spenders:
            view[i:i+len(s)] = s
            i += len(s)
        return SpenderHash(keccak_1600(bytes(view))[:self.truncate])

    def encode(self) -> bytes:
        if not self.spenders or not self.threshold:
            raise ValueError("Cannot encode empty SpenderList.")
        if len(self.spenders) == 1:
            return self.spenders[0].encode()
        prefix = [(self.truncate << 2) | SPENDER_LIST]
        if len(self.spenders) < 128:
            prefix.append(len(self.spenders) << 1)
        else:
            prefix.append(((len(self.spenders) & 0x7f) << 1) | 1)
            prefix.append((len(self.spenders) >> 7) & 0xff)
        if self.threshold < 128:
            prefix.append(self.threshold << 1)
        else:
            prefix.append(((self.threshold & 0x7f) << 1) | 1)
            prefix.append((self.threshold >> 7) & 0xff)
        prefix = bytes(prefix)
        spenders = [s.encode() for s in self.spenders]
        buffer = bytearray(len(prefix) + sum(len(s) for s in spenders))
        view = memoryview(buffer)
        i = 0
        view[i:i+len(prefix)] = prefix
        i += len(prefix)
        for s in spenders:
            view[i:i+len(s)] = s
            i += len(s)
        return bytes(view)


class Block(object):

    def __init__(self,
        timestamp: int,                         # microseconds since UNIX epoch
        prev_hash: bytes,                       # hash digest of most recent block
        uid: SpenderHash,                       # uid of block worker
        nonce: bytes | None = None,             # nonce required to hash block to target difficulty
        parameters: bytes | None = None,        # epoch blocks publish network parameters
        payments: list[bytes] = []              # payment transactions to commit by this block
    ):
        self.timestamp = timestamp
        self.prev_hash = prev_hash
        self.uid = uid
        self.nonce = nonce
        self.parameters = parameters
        self.payments = payments

    @cached_property
    def header_prehash(self) -> bytes:
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

    @cached_property
    def digest(self) -> bytes | None:
        if not self.nonce:
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
