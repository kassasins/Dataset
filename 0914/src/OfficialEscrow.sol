pragma solidity 0.8.26;

import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {IUnlockCallback} from "@uniswap/v4-core/src/interfaces/callback/IUnlockCallback.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";

contract OfficialEscrow is IUnlockCallback {
    error Unauthorized();
    error TransferFailed();

    IPoolManager public immutable poolManager;
    address payable public immutable official;

    constructor(IPoolManager poolManager_, address payable official_) {
        poolManager = poolManager_;
        official = official_;
    }

    receive() external payable {
        if (msg.sender != address(poolManager)) revert Unauthorized();
    }

    function claim() external returns (uint256 amount) {
        if (msg.sender != official) revert Unauthorized();
        uint256 claimAmount = poolManager.balanceOf(address(this), 0);
        if (claimAmount != 0) poolManager.unlock(abi.encode(claimAmount));
        amount = address(this).balance;
        if (amount != 0) {
            (bool success,) = official.call{value: amount}("");
            if (!success) revert TransferFailed();
        }
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert Unauthorized();
        uint256 amount = abi.decode(data, (uint256));
        poolManager.burn(address(this), 0, amount);
        poolManager.take(Currency.wrap(address(0)), address(this), amount);
        return "";
    }
}
