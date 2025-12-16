"""
Async LLM Batcher for concurrent request handling.

V2.2 (Dynamic Capability Fix #6): Provides a batching layer on top of async LLM clients
to handle concurrent requests across multiple runs efficiently.

The AsyncLLMBatcher class:
- Queues LLM requests from concurrent runs
- Batches requests to reduce API overhead
- Applies rate limiting to avoid quota exhaustion
- Provides request deduplication for identical prompts
- Tracks batch-level metrics for observability

This addresses the concurrent execution bottleneck where multiple graph nodes
making LLM calls would compete for rate limits.

Example usage:
    batcher = AsyncLLMBatcher(max_concurrent=5, batch_delay_ms=50)
    
    async with batcher:
        result = await batcher.submit("Generate client code", task_type="codegen")
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable, Tuple
from collections import OrderedDict
from contextlib import asynccontextmanager

from integration_coworker.llm.async_client import get_async_llm_client, call_llm_async

logger = logging.getLogger(__name__)


@dataclass
class BatchRequest:
    """A single request in the batch queue."""
    request_id: str
    prompt: str
    task_type: str
    system_prompt: Optional[str]
    temperature: Optional[float]
    max_tokens: Optional[int]
    future: asyncio.Future = field(default_factory=asyncio.Future)
    submitted_at: float = field(default_factory=time.time)
    
    def get_cache_key(self) -> str:
        """Get a cache key for deduplication."""
        content = f"{self.prompt}:{self.system_prompt or ''}:{self.task_type}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class BatchMetrics:
    """Metrics for batch operations."""
    total_requests: int = 0
    deduplicated_requests: int = 0
    completed_requests: int = 0
    failed_requests: int = 0
    total_wait_time_ms: float = 0.0
    total_processing_time_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "deduplicated_requests": self.deduplicated_requests,
            "completed_requests": self.completed_requests,
            "failed_requests": self.failed_requests,
            "avg_wait_time_ms": self.total_wait_time_ms / max(1, self.completed_requests),
            "avg_processing_time_ms": self.total_processing_time_ms / max(1, self.completed_requests),
        }


class AsyncLLMBatcher:
    """
    Batches async LLM requests for efficient concurrent execution.
    
    V2.2 (Fix #6): This class addresses concurrent execution bottlenecks by:
    
    1. **Request Queuing**: All LLM requests go through a central queue
    2. **Rate Limiting**: Enforces max concurrent requests to avoid quota issues
    3. **Deduplication**: Identical prompts share the same response
    4. **Batching**: Groups requests to reduce overhead
    5. **Metrics**: Tracks performance for observability
    
    Usage:
        # As context manager (recommended)
        async with AsyncLLMBatcher() as batcher:
            tasks = [
                batcher.submit("prompt1", task_type="codegen"),
                batcher.submit("prompt2", task_type="codegen"),
                batcher.submit("prompt3", task_type="codegen"),
            ]
            results = await asyncio.gather(*tasks)
        
        # Manual lifecycle
        batcher = AsyncLLMBatcher()
        await batcher.start()
        try:
            result = await batcher.submit("prompt", task_type="codegen")
        finally:
            await batcher.stop()
    """
    
    def __init__(
        self,
        max_concurrent: int = 5,
        batch_delay_ms: int = 50,
        max_batch_size: int = 10,
        enable_deduplication: bool = True,
        request_timeout_seconds: float = 120.0,
    ):
        """
        Initialize the batcher.
        
        Args:
            max_concurrent: Maximum concurrent LLM calls (rate limiting)
            batch_delay_ms: Delay before processing a batch (allows accumulation)
            max_batch_size: Maximum requests in a single batch
            enable_deduplication: Whether to deduplicate identical prompts
            request_timeout_seconds: Timeout for individual requests
        """
        self.max_concurrent = max_concurrent
        self.batch_delay_ms = batch_delay_ms
        self.max_batch_size = max_batch_size
        self.enable_deduplication = enable_deduplication
        self.request_timeout_seconds = request_timeout_seconds
        
        # Queue and state
        self._queue: asyncio.Queue[BatchRequest] = asyncio.Queue()
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._running: bool = False
        self._processor_task: Optional[asyncio.Task] = None
        
        # Deduplication cache: cache_key -> (result, futures_waiting)
        self._dedup_cache: Dict[str, str] = {}
        self._pending_dedup: Dict[str, List[asyncio.Future]] = {}
        
        # Metrics
        self.metrics = BatchMetrics()
        
        # Request counter for unique IDs
        self._request_counter: int = 0
    
    async def start(self) -> None:
        """Start the batch processor."""
        if self._running:
            return
        
        self._running = True
        self._processor_task = asyncio.create_task(self._process_queue())
        logger.info(f"AsyncLLMBatcher started (max_concurrent={self.max_concurrent})")
    
    async def stop(self) -> None:
        """Stop the batch processor and wait for pending requests."""
        if not self._running:
            return
        
        self._running = False
        
        # Wait for queue to drain
        if not self._queue.empty():
            logger.info(f"Waiting for {self._queue.qsize()} pending requests...")
            await self._queue.join()
        
        # Cancel processor task
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        
        logger.info("AsyncLLMBatcher stopped")
    
    async def __aenter__(self) -> 'AsyncLLMBatcher':
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()
    
    def _generate_request_id(self) -> str:
        """Generate a unique request ID."""
        self._request_counter += 1
        return f"req_{self._request_counter}_{int(time.time() * 1000)}"
    
    async def submit(
        self,
        prompt: str,
        task_type: str = "default",
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Submit an LLM request for batched execution.
        
        Args:
            prompt: The user prompt
            task_type: Type of task for config lookup
            system_prompt: Optional system prompt
            temperature: Optional temperature override
            max_tokens: Optional max tokens override
            
        Returns:
            The LLM response text
        """
        if not self._running:
            # Fall back to direct call if batcher not running
            logger.debug("Batcher not running, making direct call")
            return await call_llm_async(
                prompt=prompt,
                task_type=task_type,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        
        # Create request
        request = BatchRequest(
            request_id=self._generate_request_id(),
            prompt=prompt,
            task_type=task_type,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        
        self.metrics.total_requests += 1
        
        # Check deduplication
        if self.enable_deduplication:
            cache_key = request.get_cache_key()
            
            # Check if result already cached
            if cache_key in self._dedup_cache:
                self.metrics.deduplicated_requests += 1
                logger.debug(f"Request {request.request_id} deduplicated (cached)")
                return self._dedup_cache[cache_key]
            
            # Check if another request with same key is pending
            if cache_key in self._pending_dedup:
                self.metrics.deduplicated_requests += 1
                logger.debug(f"Request {request.request_id} deduplicated (pending)")
                # Add our future to the waiting list
                self._pending_dedup[cache_key].append(request.future)
                return await request.future
            
            # This is the first request for this key
            self._pending_dedup[cache_key] = []
        
        # Add to queue
        await self._queue.put(request)
        
        # Wait for result with timeout
        try:
            result = await asyncio.wait_for(
                request.future,
                timeout=self.request_timeout_seconds,
            )
            
            # Cache result for deduplication
            if self.enable_deduplication:
                cache_key = request.get_cache_key()
                self._dedup_cache[cache_key] = result
                
                # Resolve any pending futures waiting for this key
                if cache_key in self._pending_dedup:
                    for waiting_future in self._pending_dedup[cache_key]:
                        if not waiting_future.done():
                            waiting_future.set_result(result)
                    del self._pending_dedup[cache_key]
            
            return result
            
        except asyncio.TimeoutError:
            logger.error(f"Request {request.request_id} timed out after {self.request_timeout_seconds}s")
            self.metrics.failed_requests += 1
            raise
    
    async def _process_queue(self) -> None:
        """Background task that processes the request queue."""
        while self._running:
            try:
                # Collect a batch of requests
                batch: List[BatchRequest] = []
                
                # Get first request (blocking)
                try:
                    request = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0,
                    )
                    batch.append(request)
                except asyncio.TimeoutError:
                    continue
                
                # Allow more requests to accumulate
                if self.batch_delay_ms > 0:
                    await asyncio.sleep(self.batch_delay_ms / 1000.0)
                
                # Collect additional requests up to batch size
                while len(batch) < self.max_batch_size and not self._queue.empty():
                    try:
                        request = self._queue.get_nowait()
                        batch.append(request)
                    except asyncio.QueueEmpty:
                        break
                
                # Process batch concurrently (with rate limiting)
                if batch:
                    await self._process_batch(batch)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in batch processor: {e}")
                await asyncio.sleep(0.1)
    
    async def _process_batch(self, batch: List[BatchRequest]) -> None:
        """Process a batch of requests concurrently."""
        logger.debug(f"Processing batch of {len(batch)} requests")
        
        async def _process_one(request: BatchRequest) -> None:
            """Process a single request with rate limiting."""
            async with self._semaphore:
                start_time = time.time()
                wait_time = (start_time - request.submitted_at) * 1000
                self.metrics.total_wait_time_ms += wait_time
                
                try:
                    result = await call_llm_async(
                        prompt=request.prompt,
                        task_type=request.task_type,
                        system_prompt=request.system_prompt,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                    )
                    
                    if not request.future.done():
                        request.future.set_result(result)
                    
                    self.metrics.completed_requests += 1
                    processing_time = (time.time() - start_time) * 1000
                    self.metrics.total_processing_time_ms += processing_time
                    
                except Exception as e:
                    logger.error(f"Request {request.request_id} failed: {e}")
                    if not request.future.done():
                        request.future.set_exception(e)
                    self.metrics.failed_requests += 1
                
                finally:
                    self._queue.task_done()
        
        # Process all requests in batch concurrently
        tasks = [_process_one(request) for request in batch]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics."""
        return self.metrics.to_dict()
    
    def clear_dedup_cache(self) -> None:
        """Clear the deduplication cache."""
        self._dedup_cache.clear()
        logger.debug("Deduplication cache cleared")


# Global batcher instance (singleton pattern)
_global_batcher: Optional[AsyncLLMBatcher] = None


def get_global_batcher(
    max_concurrent: int = 5,
    batch_delay_ms: int = 50,
) -> AsyncLLMBatcher:
    """
    Get the global batcher instance (creates if not exists).
    
    This provides a singleton pattern for the batcher, allowing
    multiple parts of the application to share the same rate-limited
    queue.
    
    Args:
        max_concurrent: Maximum concurrent LLM calls
        batch_delay_ms: Delay before processing a batch
        
    Returns:
        The global AsyncLLMBatcher instance
    """
    global _global_batcher
    
    if _global_batcher is None:
        _global_batcher = AsyncLLMBatcher(
            max_concurrent=max_concurrent,
            batch_delay_ms=batch_delay_ms,
        )
    
    return _global_batcher


async def reset_global_batcher() -> None:
    """Reset the global batcher (for testing)."""
    global _global_batcher
    
    if _global_batcher is not None:
        await _global_batcher.stop()
        _global_batcher = None


@asynccontextmanager
async def batched_llm_context(
    max_concurrent: int = 5,
    batch_delay_ms: int = 50,
):
    """
    Context manager for batched LLM calls.
    
    Usage:
        async with batched_llm_context() as batcher:
            results = await asyncio.gather(
                batcher.submit("prompt1", task_type="codegen"),
                batcher.submit("prompt2", task_type="codegen"),
            )
    """
    batcher = AsyncLLMBatcher(
        max_concurrent=max_concurrent,
        batch_delay_ms=batch_delay_ms,
    )
    async with batcher:
        yield batcher
