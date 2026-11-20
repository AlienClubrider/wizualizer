package stalls;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.net.URI;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/** One local of each JDK collection and value type the recorder reads from fields. */
public final class Census {
  private Census() {}

  public static int count() {
    List<String> list = new ArrayList<>(List.of("a", "b"));
    List<Integer> fixed = List.of(1, 2, 3);
    List<String> one = List.of("solo");
    Set<String> sorted = new TreeSet<>(Set.of("y", "x"));
    Set<String> hashed = new HashSet<>(List.of("h"));
    Map<String, Integer> hash = new HashMap<>(Map.of("k", 1));
    Map<String, Integer> linked = new LinkedHashMap<>();
    linked.put("first", 1);
    linked.put("second", 2);
    Map<String, Integer> tree = new TreeMap<>(Map.of("b", 2, "a", 1));
    Map<String, Integer> small = Map.of("only", 9);
    Map<String, Integer> concurrent = new ConcurrentHashMap<>(Map.of("c", 3));
    Deque<String> deque = new ArrayDeque<>(List.of("p", "q"));
    List<String> chain = new LinkedList<>(List.of("l1", "l2"));
    Collection<Integer> values = linked.values();
    Set<String> keys = linked.keySet();
    List<String> view = Collections.unmodifiableList(list);
    List<Integer> many = new ArrayList<>(List.of(1, 2, 3, 4, 5, 6, 7, 8, 9, 10));
    BigDecimal price = new BigDecimal("12.50");
    BigInteger big = new BigInteger("-123456789012345678901234567890");
    LocalDate day = LocalDate.of(2026, 11, 20);
    Instant epoch = Instant.ofEpochSecond(0);
    Duration wait = Duration.ofMinutes(90);
    UUID id = new UUID(1, 2);
    URI uri = URI.create("https://example.com/a?b=c");
    return list.size() + fixed.size() + one.size() + sorted.size() + hashed.size() + hash.size() + tree.size()
        + small.size() + concurrent.size() + deque.size() + chain.size() + values.size() + keys.size() + view.size()
        + many.size() + price.scale() + big.signum() + day.getDayOfMonth() + (int) epoch.getEpochSecond()
        + (int) wait.toMinutes() + (int) id.getLeastSignificantBits() + uri.getPort();
  }
}
