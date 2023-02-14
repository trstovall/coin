
# Arka: A private and scalable transaction chain with monetary policy voting

## Bank

### Send / Request Payment (Layer 1)

### Send / Request Receipt (Layer 2)

### Manage accounts and receipts (Layer 3)

- Create
- Organize
- Export
- Import

## Chain consensus with PoW

### Block link

    ---
    # common to blocks and payment links
    index: 0                                    # block since genesis block
    prev_block: ...keccak800...                 # 32-byte digest of previous block link in chain
    prev_link: ...keccak800...                  # 32-byte digest of previous block or payment link in chain
    timestamp: 0                                # seconds since UNIX epoch, unique

    # common to all blocks
    total_work: 0                               # integer sum of work since chain start
    worker: ...ed25519 key...                   # worker's public key - all proceeds from this tx go to key
    target: 2 ** 32                             # 2-byte float from 0 to 2**256-1 ([1-256] * 2 ** [0-248] - 1)
    nonce: 1-32 bytes binary                    # H(H(block - {target, nonce}) | target | nonce) ~= 0

    # exists only in epoch blocks (every 10000 blocks)
    parameters:                                 # Adjusted parameters for epoch (10000 blocks)
      target: 2 ** 32                           # difficulty to mint a block link (average # of hashes)
      block_reward: 1000 * 2 ** 32              # 1000 coin minted by block link
      stamp_reward: 500 * 2 ** 32               # reward = (stamp_reward * workstamp.target) / target
      data_fee: 2 ** 20                         # 2 ** 20 units per byte in each transaction is to be destroyed
      expiry: 100                               # forget transactions older than 100 epochs
    
### Payment link

    ---
    # common to blocks and payment links
    index: 0                                    # links since block link
    prev_block: ...keccak800...                 # 32-byte digest of most recent block in chain
    prev_link: ...keccak800...                  # 32-byte digest of most recent link in chain
    timestamp: 0                                # seconds since UNIX epoch, unique

    # payment links are signed by prev_block.worker
    signature: ...ed25519 signature...          # 64-byte result of ed25519-sign of link digest

    # each payment link includes a list of payments
    payments:                                   # identifies link as a payment link

    - from:                                     # sources are non-expired UTXOs and workstamps

      # spend a UTXO
      - digest: ...keccak800...                 # block digest or H(link digest | payment index | output index)
        key: ...ed25519 key...                  # Present key that hashes to output address
        signature: ...ed25519 signature...      # payment outputs signed by key

      # spend a workstamp
      - key: ...ed25519 key...                  # ed25519 key used to spend workstamp
        target: 2**32                           # 2-byte float from 0 to 2**256-1
        nonce: 0-32 bytes binary                # H(key | target | nonce) ~= 0, unique
        signature: ...ed25519 signature...      # Payment outputs signed by key

      to:                                       # destinations are UTXOs

      # create a UTXO
      - address: 0-32 bytes keccak800 digest    # Receipients public ed25519 key hashed
        units: 0                                # 1 coin = 2**32 units
        memo: 0-MEMO_LIMIT bytes binary         # can be used for layer 2 protocols
        # vote to adjust parameters, weighted by units and aggregated across epoch (10000 blocks)
        vote:                                   # applied to each entry in `tx.payment.to`
          block_reward: 0                       # [-128:127] integer corresponding to +/- 10% adjustment
          stamp_reward: 0                       # [-128:127] integer corresponding to +/- 10% adjustment
          data_fee: 0                           # [-128:127] integer corresponding to +/- 10% adjustment
          expiry: 0                             # [-128:127] integer corresponding to +/- 10% adjustment

      # commit data
      - memo: 0-MEMO_LIMIT bytes binary         # can be used for layer 2 protocols


### Consensus

The transaction chain is a linked list which begins with a particular authority transaction for the given chain followed by a sequence of payment transactions and authority transactions.

Authority transactions are generated periodically by random peers performing computational work.  If the hash digest of a valid authority transaction is a member of a relatively small set determined by the work `difficulty` consensus parameter, then the transaction represents a valid proof of work.  The elected `authority` then generates payment transactions, signing any pending payments and appending them to the transaction chain until a new authority transaction is added.  Miners, computational units aimed at generating transactions, should listen for transactions and adjust their own authority transactions accordingly.

To compute the hash digest of an authority transaction, deterministicly represent the transaction object, minus the `nonce` field, as a binary string.  Hash the string with SHAKE-256.  Then, concatenate the intermediate hash digest with the `nonce` and hash again with SHA256.  Difficulty consists of an 8-bit `base` and 8-bit `exp` such that `difficulty == (base + 1) * (2 ** exp) - 1`.  The proof of work is valid if the first 8-bit byte of the final hash digest is numerically greater than or equal to `base` and is followed by at least `exp` number of zero bits.

Transactions provide a `chain` field, which is the hash digest of the most recent previous transaction, forming a linked list transaction chain.  The transaction also provides a `prev_authority` field, which is the hash digest of the most recent previous authority transaction, forming a second chain that nodes can use to bootstrap their local chain data.  `offset` defines the number of milliseconds elapsed since the first miner began processing the transaction chain and is used to compute `difficulty` adjustments every 4096 blocks.  `difficulty` is adjusted proportionally to the time elapsed since the most recent previous adjustment (`new_difficulty := auth_period * sum(difficulty) / (2 * (median(clock) - old_clock))`).

The `payment` structure defines a set of unspent transaction outputs from previous transactions, and redistributes the total balance, minus transaction data tax, to a new set of outputs.  The `payment` structure also includes a set of votes which determine chain consensus parameters.  


## Network

    Packet(mtu=1300):
      sent: PacketHash
      to: Identifier
      from: Identifier | None
      timestamp: datetime
      nonce: bytes(16)
      data: decrypted(EncryptedFragments)

    Identifier = hash(PublicKey)

    EncryptedFragments = encrypted(list[Message | Fragment])

    Fragment:
      hash: FragmentHash
      message:
        hash: MessageHash
        nparts: int
      part: int
      data: bytes(range(64, 1024, 64)[0])
    
    Message = (
      TraceRequest | TraceResponse | ConnectRequest | ConnectResponse
      | Get | Put | JoinRequest | JoinResponse | LeaveRequest | LeaveResponse
      | ReserveRequest | ReserveResponse | SendRequest | SendResponse
      | Payment | Transaction | PaymentFragment | SignPayment | RevealCommits
    )

### UDP Hole punching

    <Registry>:
      id: <PublicKey>
      host: <static public IPv4 or DNS address>
      port: <UDP port for arka protocol>
      timestamp: <datetime>
      signature: <Signature>

    <UserAddress>:
      id: <PublicKey>
      host: <static or ephemeral public IPv4 or DNS address>
      port: <UDP port for arka protocol>
      timestamp: <datetime>
      signature: <Signature>

    <RegistryPayment>:
      to:
        units: <int>
        memo:
          type: user
          user: <UserAddress.id>
          veto: False
      from:
      - <UnspentTransactionOutput>

    <Register>:
      init:
      - <Registry>
      - <RegistryPayment>
      sequence:
      - <StartPacket from=predecessor to=registry data=GetAddress,RegistryPayment>
      - <ContinuePacket from=registry to=predecessor data=HasAddress>
      - <UserAddress>
      - <ContinuePacket from=predecessor to=registry data=GetSuccessor,UserAddress>
      - <ContinuePacket from=registry to=predecessor data=HasSuccessor>
      - <ContinuePacket from=registry to=successor data=HasSuccessor>
      - <StartPacket from=predecessor to=successor data=GetSuccessor>
      - <StartPacket from=successor to=predecessor data=GetSuccessor>
      - <ContinuePacket from=predecessor to=successor data=HasSuccessor>
      - <ContinuePacket to=predecessor data=HasSuccessor>
      

### DC network for broadcasting payments and transactions

Based on [Herbivore](https://www.cs.cornell.edu/people/egs/herbivore/herbivore.pdf)
