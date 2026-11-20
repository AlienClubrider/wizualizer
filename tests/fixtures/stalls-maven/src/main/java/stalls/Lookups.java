package stalls;

import java.util.Optional;
import java.util.stream.IntStream;

/** A cache miss whose fallback, run inside Optional.orElseGet, uses Optional thousands of times. */
final class Lookups {
  private Lookups() {}

  static long find(String key) {
    return Optional.ofNullable(cached(key)).orElseGet(() -> scan(2000));
  }

  private static Long cached(String key) {
    return null;
  }

  static long scan(int n) {
    return IntStream.range(0, n).mapToObj(Optional::of).filter(Optional::isPresent).map(Optional::get).count();
  }
}
