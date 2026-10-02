# Reference outputs for the methods of compare.existence (SPEC §9.5, §13.4; D321).
#
# Run with base R, no packages:  Rscript existence.R > existence.json
# The checked-in existence.json was written by R 4.3.3. The Wilson interval is prop.test's
# (correct = FALSE); Newcombe's hybrid score interval (method 10 of Newcombe 1998) and the Katz log
# interval are written out below, as named implementations; Fisher's exact test is fisher.test's,
# the chi-squared test chisq.test's (correct = FALSE), its tail pchisq's, and q-values
# p.adjust's (method = "BH").

num <- function(x) if (is.na(x)) "null" else sprintf("%.17g", x)
vec <- function(x) paste0("[", paste(vapply(x, num, ""), collapse = ", "), "]")
obj <- function(...) {
  parts <- list(...)
  paste0("{", paste(sprintf("\"%s\": %s", names(parts), unlist(parts)), collapse = ", "), "}")
}

wilson <- function(x, n, level) {
  unname(suppressWarnings(prop.test(x, n, conf.level = level, correct = FALSE))$conf.int[1:2])
}

newcombe <- function(x1, n1, x2, n2, level) {
  p1 <- x1 / n1
  p2 <- x2 / n2
  w1 <- wilson(x1, n1, level)
  w2 <- wilson(x2, n2, level)
  d <- p1 - p2
  c(d, d - sqrt((p1 - w1[1])^2 + (w2[2] - p2)^2), d + sqrt((w1[2] - p1)^2 + (p2 - w2[1])^2))
}

katz <- function(x1, n1, x2, n2, level) {
  ratio <- (x1 / n1) / (x2 / n2)
  error <- sqrt(1 / x1 - 1 / n1 + 1 / x2 - 1 / n2)
  q <- qnorm((1 + level) / 2)
  c(ratio, exp(log(ratio) - q * error), exp(log(ratio) + q * error))
}

lines <- character()
add <- function(line) lines <<- c(lines, line)

proportions <- list(
  c(81, 263), c(0, 10), c(10, 10), c(1, 2), c(56, 70), c(48, 80), c(5, 1000), c(1, 1),
  c(999, 1000), c(123456, 250000)
)
levels <- c(0.95, 0.9, 0.99, 0.5)
entries <- character()
for (p in proportions) for (level in levels) {
  w <- wilson(p[1], p[2], level)
  entries <- c(entries, obj(x = num(p[1]), n = num(p[2]), level = num(level), low = num(w[1]),
                            high = num(w[2])))
}
add(sprintf("\"wilson\": [%s]", paste(entries, collapse = ",\n  ")))

pairs <- list(
  c(56, 70, 48, 80), c(9, 10, 3, 10), c(6, 7, 2, 7), c(5, 56, 0, 29), c(0, 10, 0, 20),
  c(0, 10, 0, 10), c(10, 10, 0, 20), c(10, 10, 0, 10), c(3, 8, 7, 9), c(400, 1000, 380, 1100)
)
entries <- character()
for (p in pairs) for (level in c(0.95, 0.9)) {
  d <- newcombe(p[1], p[2], p[3], p[4], level)
  entries <- c(entries, obj(x1 = num(p[1]), n1 = num(p[2]), x2 = num(p[3]), n2 = num(p[4]),
                            level = num(level), difference = num(d[1]), low = num(d[2]),
                            high = num(d[3])))
}
add(sprintf("\"newcombe\": [%s]", paste(entries, collapse = ",\n  ")))

pairs <- list(c(56, 70, 48, 80), c(9, 10, 3, 10), c(5, 56, 3, 29), c(1, 1, 1, 2),
              c(400, 1000, 380, 1100), c(7, 7, 7, 7))
entries <- character()
for (p in pairs) for (level in c(0.95, 0.99)) {
  k <- katz(p[1], p[2], p[3], p[4], level)
  entries <- c(entries, obj(x1 = num(p[1]), n1 = num(p[2]), x2 = num(p[3]), n2 = num(p[4]),
                            level = num(level), ratio = num(k[1]), low = num(k[2]),
                            high = num(k[3])))
}
add(sprintf("\"katz\": [%s]", paste(entries, collapse = ",\n  ")))

tables <- list(
  c(3, 1, 1, 3), c(10, 2, 3, 15), c(0, 5, 5, 0), c(1, 9, 11, 3), c(100, 200, 150, 120),
  c(2000, 3000, 2100, 2900), c(7, 0, 0, 0), c(0, 0, 4, 6), c(5, 5, 5, 5), c(1, 0, 0, 1),
  c(12, 30, 0, 45), c(50000, 50000, 49000, 51000), c(3, 40, 30, 4), c(2, 4, 3, 1),
  c(1, 13, 16, 14), c(2, 3, 3, 2), c(1, 5, 9, 2), c(1887, 1414, 2946, 152),
  c(2640, 2423, 170, 1822)
)
entries <- character()
for (t in tables) {
  m <- matrix(t, 2, byrow = TRUE)
  entries <- c(entries, obj(table = vec(t), p = num(fisher.test(m)$p.value)))
}
add(sprintf("\"fisher\": [%s]", paste(entries, collapse = ",\n  ")))

tables <- list(
  list(3, c(10, 20, 15, 25, 30, 5)), list(2, c(12, 5, 7, 9)), list(4, c(1, 2, 3, 4, 5, 6, 7, 8)),
  list(6, c(30, 70, 25, 75, 40, 60, 10, 90, 50, 50, 33, 67)),
  list(2, c(1000, 2000, 1500, 2600)), list(3, c(0, 5, 3, 2, 7, 1))
)
entries <- character()
for (t in tables) {
  m <- matrix(t[[2]], t[[1]], byrow = TRUE)
  test <- suppressWarnings(chisq.test(m, correct = FALSE))
  entries <- c(entries, obj(rows = num(t[[1]]), table = vec(t[[2]]),
                            statistic = num(unname(test$statistic)),
                            df = num(unname(test$parameter)), p = num(test$p.value)))
}
add(sprintf("\"chi_squared\": [%s]", paste(entries, collapse = ",\n  ")))

tails <- list(c(3.84, 1), c(0.001, 1), c(100, 1), c(500, 3), c(7, 10), c(0.5, 2), c(50, 30),
              c(1e-5, 4), c(1400, 5), c(2, 0.5), c(25, 25), c(0.1, 60))
entries <- character()
for (t in tails) {
  entries <- c(entries, obj(x = num(t[1]), df = num(t[2]),
                            q = num(pchisq(t[1], t[2], lower.tail = FALSE))))
}
add(sprintf("\"upper_tail\": [%s]", paste(entries, collapse = ",\n  ")))

vectors <- list(c(0.01, 0.02, 0.03, 0.04, 0.05), c(0.01, 0.04, 0.03), c(0.5),
                c(0.2, 0.2, 0.01, 0.9, 0.049), c(1e-8, 0.3, 0.3, 0.7, 0.0004, 0.02))
entries <- character()
for (v in vectors) {
  entries <- c(entries, obj(p = vec(v), q = vec(p.adjust(v, method = "BH"))))
}
add(sprintf("\"benjamini_hochberg\": [%s]", paste(entries, collapse = ",\n  ")))

cat(sprintf("{\"r\": \"%s\",\n%s}\n", R.version.string, paste(lines, collapse = ",\n")))
