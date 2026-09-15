// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

interface ITremor {
    function totalSupply() external view returns (uint256);
    function burnFrom(address from, uint256 v) external;
}

// ┌────────────────────────────────────────────────────────────────────────────┐
// │                                                                            │
// │    T  R  E  M  O  R                                                        │
// │                                                                            │
// │    tremorevm.xyz                                                           │
// │                                                                            │
// │    x.com/tremorEVM                                                         │
// │                                                                            │
// └────────────────────────────────────────────────────────────────────────────┘
//
//  Fund.sol  ·  THE FUND
//  ──────────────────────
//  Fed by the hook. Drawn from by the market. Burned against by holders.
//
//
//  WHAT COMES IN
//
//      The swap skim, taken from the PoolManager straight to this address by
//      the hook. Nothing else, ever.
//
//      The credit is REFUSED unless the ETH is already here. The books cannot
//      be told about money the contract does not hold, which forecloses an
//      entire class of bug for the price of one comparison.
//
//  WHERE IT GOES
//
//      Two places, and they are the only two that exist.
//
//      THE SUBSIDY is drawn by the market at settlement and paid out to
//      everyone who took a side that epoch, win or lose. This is what makes
//      the market worth starting at all: taking either side is positive
//      expectancy before any view on volatility is expressed.
//
//      THE FLOOR is what any holder may claim a pro-rata share of, by burning.
//
//  WHAT CANNOT HAPPEN
//
//      A withdrawal. A sweep to a treasury. An owner. An upgrade. Not
//      withheld — absent. The functions do not exist to be called.
//
//  THE ONE INVARIANT
//
//      address(this).balance  >=  subsidyPool + floorPool
//
//      Enforced on the way IN rather than audited afterwards. Equality is the
//      normal state; the balance can only run ahead of the books when ETH
//      arrives without a credit, and {sync} hands that to the floor.
//
//  WHY THE FLOOR IS NEUTRAL
//
//      Burning n tokens for n × floor ÷ supply leaves floor ÷ supply exactly
//      where it was. An exit through the floor never dilutes the holders who
//      stay, and the only thing that moves it upward is more fee income
//      arriving.
//
//  HOW IT FAILS
//
//      It starts at zero and grows only with volume. A pool nobody trades has
//      no subsidy to pay and no floor to stand on, and this contract has no
//      way to manufacture either.
//
contract Fund {
    ITremor public immutable TOKEN;
    address public immutable TOKEN_ADDR;

    /// @notice The share of every credit that goes to the subsidy.
    uint16 public immutable SUBSIDY_BPS;

    /// @notice Waiting to be drawn into an epoch pot.
    uint128 public subsidyPool;
    /// @notice Standing under the token.
    uint128 public floorPool;
    /// @notice Every wei ever credited, for anyone reconciling the fund.
    uint128 public creditedEver;
    /// @notice Every wei ever paid out through the floor.
    uint128 public redeemedEver;
    /// @notice Every wei ever drawn into an epoch pot.
    uint128 public subsidisedEver;

    address public hook;
    address public market;
    address public wirer;

    event Credited(uint128 toSubsidy, uint128 toFloor);
    event SubsidyDrawn(address indexed to, uint128 amount);
    event Redeemed(address indexed who, uint256 burned, uint128 ethOut, uint256 floorAfter);
    event Wired(address hook, address market);

    error NotHook();
    error NotMarket();
    error NotWirer();
    error AlreadyWired();
    error ZeroAddress();
    error BadConfig();
    error Unfunded(uint256 held, uint256 needed);
    error NothingToRedeem();
    error PayFailed();
    error Reentrant();

    uint8 private unlocked_ = 1;
    modifier lock() {
        if (unlocked_ != 1) revert Reentrant();
        unlocked_ = 2;
        _;
        unlocked_ = 1;
    }

    constructor(address _token, uint16 _subsidyBps, address _wirer) {
        if (_token == address(0) || _wirer == address(0)) revert ZeroAddress();
        if (_subsidyBps > 10_000) revert BadConfig();
        TOKEN = ITremor(_token);
        TOKEN_ADDR = _token;
        SUBSIDY_BPS = _subsidyBps;
        wirer = _wirer;
    }

    function wire(address _hook, address _market) external {
        if (msg.sender != wirer) revert NotWirer();
        if (hook != address(0)) revert AlreadyWired();
        if (_hook == address(0) || _market == address(0)) revert ZeroAddress();
        hook = _hook;
        market = _market;
        wirer = address(0);
        emit Wired(_hook, _market);
    }

    // ── what comes in ────────────────────────────────────────────────────

    /// @notice Book a skim the hook has already delivered here. Refused unless
    ///         the ETH is present — see THE ONE INVARIANT.
    function credit(uint128 amount) external {
        if (msg.sender != hook) revert NotHook();
        uint256 accounted = uint256(subsidyPool) + floorPool;
        if (address(this).balance < accounted + amount) {
            revert Unfunded(address(this).balance, accounted + amount);
        }
        uint128 toSub = uint128((uint256(amount) * SUBSIDY_BPS) / 10_000);
        uint128 toFloor = amount - toSub;
        unchecked {
            subsidyPool += toSub;
            floorPool += toFloor;
            creditedEver += amount;
        }
        emit Credited(toSub, toFloor);
    }

    /// @notice Hand any ETH sitting here that the books do not know about to
    ///         the FLOOR. Permissionless. Someone who wants to donate to the
    ///         holders simply sends ETH and calls this.
    function sync() external returns (uint128 credited) {
        uint256 accounted = uint256(subsidyPool) + floorPool;
        uint256 bal = address(this).balance;
        if (bal <= accounted) return 0;
        unchecked {
            credited = uint128(bal - accounted);
            floorPool += credited;
            creditedEver += credited;
        }
        emit Credited(0, credited);
    }

    // ── the subsidy ──────────────────────────────────────────────────────

    /// @notice The market draws the whole standing subsidy into the pot it is
    ///         settling. Roughly an epoch's fees, because that is how long it
    ///         had to accumulate.
    function drawSubsidy() external lock returns (uint128 amount) {
        if (msg.sender != market) revert NotMarket();
        amount = subsidyPool;
        if (amount == 0) return 0;
        subsidyPool = 0;
        unchecked { subsidisedEver += amount; }
        (bool ok,) = market.call{value: amount}("");
        if (!ok) revert PayFailed();
        emit SubsidyDrawn(market, amount);
    }

    // ── 3.2 / 3.4 · the floor ────────────────────────────────────────────

    /// @notice ETH per whole TREMOR the floor pays right now, 1e18-scaled.
    function floorPerToken() public view returns (uint256) {
        uint256 s = TOKEN.totalSupply();
        if (s == 0) return 0;
        return (uint256(floorPool) * 1e18) / s;
    }

    /// @notice Burn TREMOR for a pro-rata share of the floor. Requires an
    ///         allowance to this contract, which is one approval rather than a
    ///         transfer-then-trust.
    function redeem(uint256 amount) external lock returns (uint128 ethOut) {
        if (amount == 0) revert NothingToRedeem();
        uint256 supply = TOKEN.totalSupply();
        if (supply == 0) revert NothingToRedeem();

        ethOut = uint128((uint256(floorPool) * amount) / supply);
        if (ethOut == 0) revert NothingToRedeem();

        // burn FIRST, so the supply the next redeemer divides by is already
        // reduced and the floor per token cannot be double-counted
        TOKEN.burnFrom(msg.sender, amount);
        unchecked {
            floorPool -= ethOut;
            redeemedEver += ethOut;
        }

        (bool ok,) = msg.sender.call{value: ethOut}("");
        if (!ok) revert PayFailed();
        emit Redeemed(msg.sender, amount, ethOut, floorPerToken());
    }

    // ── the books ────────────────────────────────────────────────────────

    function books() external view returns (uint256 held, uint256 owed, bool ties) {
        held = address(this).balance;
        owed = uint256(subsidyPool) + floorPool;
        ties = held == owed;
    }

    /// @dev Accepts ETH silently, because it has to: the hook has the
    ///      PoolManager send the skim directly here, and refusing it would
    ///      revert every swap on the pool. Anything that arrives without a
    ///      matching {credit} is handed to the floor by {sync}.
    receive() external payable {}
}