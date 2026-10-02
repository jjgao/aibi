# Reference outputs for survival.km's Cox fit of cohorts and its test of proportional hazards
# (SPEC §9.5; D355–D357).
#
# Run with the survival package:  Rscript cox.R > cox.json
# The checked-in cox.json was written by R 4.3.3 with survival 3.5-8.
#
# "fits": each case's cohorts with events, the first the reference, and the ones the risk sets
# tie to it both ways (D356, found here by its own reachability over the risk sets); coxph's fit
# of those units alone from 0 (ties = "efron"; Surv(time, status), or Surv(entry, time, status)
# where the case has entries, so that coxph.fit or agreg.fit fits it as R chooses), its
# coefficients (null where coxph drops one as singular), standard errors, log-likelihoods,
# iterations and whether it converged (no "Ran out of iterations" warning), or the error with
# which agreg.fit stops (its exp overflow, as it recentres the linear predictor), and cox.zph's
# global test of that fit (transform = "km"), null where cox.zph fails on a singular information;
# and
# "optimum", the fit of the highest log-likelihood that converged with no coefficient dropped,
# from 0 and from every coefficient at -10, -8, ..., 10, refitted from there to eps = 1e-13, with
# its cox.zph test. Times are distinct beyond the tolerance of survival's timefix, or equal, so
# that it changes nothing.
#
# "edges": every set of edges h -> e (group h at risk at an event of group e, h != e) over 2, 3
# and 4 groups, each realised with delayed entry: for each group a window in which one of its
# units has an event alone, and for each edge a window in which a unit of e has an event while a
# unit of h is at risk, each window (k - 0.5, k + 0.25] apart from the others. Of each, coxph's
# fit of every group from 0 and from init = 0.7, 1.4, ... (iter.max = 30, eps = 1e-11), its
# coefficients to 6 digits, null where agreg.fit stops on an exp overflow.

suppressPackageStartupMessages(library(survival))

num <- function(x, digits = 17) {
  if (length(x) == 0 || is.na(x) || !is.finite(x)) "null" else sprintf("%.*g", digits, x)
}
vec <- function(x, digits = 17) {
  paste0("[", paste(vapply(x, function(v) num(v, digits), ""), collapse = ", "), "]")
}
obj <- function(...) {
  parts <- list(...)
  paste0("{", paste(sprintf("\"%s\": %s", names(parts), unlist(parts)), collapse = ", "), "}")
}

# The groups in the reference's strongly connected component of the risk sets' graph (D356).
estimated <- function(group, time, status, entry, reference) {
  levels <- sort(unique(group))
  n <- length(levels)
  reach <- diag(n) == 1
  for (t in unique(time[status == 1])) {
    dying <- unique(group[status == 1 & time == t])
    present <- unique(group[entry < t & t <= time])
    reach[match(present, levels), match(dying, levels)] <- TRUE
  }
  for (via in seq_len(n)) reach <- reach | outer(reach[, via], reach[via, ], "&")
  at <- match(reference, levels)
  levels[reach[, at] & reach[at, ]]
}

cases <- list()
case <- function(name, group, time, status, entry = NULL, iter_max = 20) {
  cases[[length(cases) + 1]] <<- list(name = name, group = group, time = time, status = status,
                                      entry = entry, iter_max = iter_max)
}

case("two groups with ties", c(1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2),
     c(1, 2, 2, 3, 4, 5, 1, 2, 3, 3, 6, 7, 8), c(0, 1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 1, 0))
case("a curve at one half over an interval", c(1, 1, 1, 1, 2, 2, 2, 2, 2),
     c(1, 2, 5, 8, 1, 3, 4, 6, 9), c(1, 1, 0, 1, 0, 1, 1, 1, 0))
case("delayed entry", c(1, 1, 1, 1, 1, 2, 2, 2, 2, 2), c(1, 2, 2, 3, 4, 5, 6, 6, 7, 8),
     c(0, 1, 1, 0, 1, 0, 1, 1, 1, 1), entry = c(0, 0, 1, 1, 2, 0, 3, 0, 0, 1))
case("an event at time zero", c(1, 1, 1, 1, 2, 2, 2, 2), c(0, 0, 3, 5, 0, 2, 4, 6),
     c(1, 0, 1, 1, 0, 1, 0, 1))
case("a group whose events all come first", c(1, 1, 1, 1, 2, 2, 2, 2),
     c(1, 2, 3, 4, 10, 11, 12, 13), c(1, 1, 1, 1, 0, 1, 0, 1))
case("a reference whose events all come first", c(2, 2, 2, 2, 1, 1, 1, 1),
     c(1, 2, 3, 4, 10, 11, 12, 13), c(1, 1, 1, 1, 0, 1, 0, 1))
case("one event time", c(1, 1, 2, 2, 2), c(3, 4, 3, 4, 4), c(1, 0, 1, 0, 0))
case("three groups, one never at risk with the others", c(1, 1, 1, 2, 2, 2, 3, 3),
     c(1, 2, 3, 1, 2, 4, 20, 21), c(1, 1, 0, 1, 1, 1, 1, 1), entry = c(0, 0, 0, 0, 0, 0, 10, 10))
halving <- list(group = c(rep(1, 11), rep(2, 5)),
                time = c(6, 13, 17, 15, 15, 19, 23, 11, 16, 9, 14, 1, 3, 6, 5, 2),
                status = c(1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1, 0))
case("a fit that halves its step twice", halving$group, halving$time, halving$status)
case("a fit that halves its step twice, with entries", halving$group, halving$time,
     halving$status, entry = rep(0, 16))
case("a fit that runs out of iterations after a worse step", halving$group, halving$time,
     halving$status, entry = rep(0, 16), iter_max = 2)
case("a fit that runs out of iterations after a better step", halving$group, halving$time,
     halving$status, entry = rep(0, 16), iter_max = 4)
case("a fit that runs out of iterations without entries", halving$group, halving$time,
     halving$status, iter_max = 3)
case("a first step that overflows", c(rep(1, 2000), rep(2, 3)), c(1:2000, 1, 1, 1), rep(1, 2003))
case("a first step that underflows, 3 against 3000", c(rep(1, 3), rep(2, 3000)),
     c(1, 1, 1, 1, rep(2:31, each = 100)[1:2999]), rep(1, 3003))
case("a first step that underflows, 1 against 1500", c(1, rep(2, 1500)),
     c(1, 1, rep(2:16, each = 100)[1:1499]), rep(1, 1501))
case("many ties among six groups", rep(1:6, each = 30), rep(c(1, 2, 3, 4, 5), 36),
     rep(c(1, 1, 0, 1, 0, 1), 30))
late <- function(cohorts, spread) {
  group <- c(rep(1, 20), rep(seq_len(cohorts) + 1, each = 201))
  time <- c(1:20, rep(c(21 + (0:199 %% spread), 1), cohorts))
  list(group = group, time = time, status = rep(1, length(time)))
}
flat <- late(2, 40)
case("a first step into a flat region", flat$group, flat$time, flat$status)
case("a first step into a flat region, with entries", flat$group, flat$time, flat$status,
     entry = rep(0, length(flat$time)))
far <- late(3, 5)
case("an estimable fit that runs out of iterations from 0", far$group, far$time, far$status)
case("an estimable fit that runs out of iterations from 0, with entries", far$group, far$time,
     far$status, entry = rep(0, length(far$time)))

set.seed(20260928)
for (index in 1:12) {
  n <- c(40, 60, 90, 120, 200, 300)[(index - 1) %% 6 + 1]
  groups <- 2 + (index - 1) %% 5
  g <- sample(seq_len(groups), n, replace = TRUE)
  t <- round(rexp(n, rate = 0.1 * (1 + (g - 1) / 2)), if (index %% 3) 1 else 0)
  s <- rbinom(n, 1, 0.75)
  e <- if (index > 6) pmax(0, round(t - runif(n) * 12, 1)) * rbinom(n, 1, 0.5) else NULL
  if (!is.null(e)) {
    keep <- e < t
    g <- g[keep]; t <- t[keep]; s <- s[keep]; e <- e[keep]
  }
  case(sprintf("random %d", index), g, t, s, entry = e)
}

fits <- character()
for (c in cases) {
  entry <- if (is.null(c$entry)) rep(-Inf, length(c$time)) else c$entry
  events <- tapply(c$status, c$group, sum)
  withevents <- as.numeric(names(events)[events > 0])
  kept <- c$group %in% withevents
  fitted <- estimated(c$group[kept], c$time[kept], c$status[kept], entry[kept], 1)
  used <- c$group %in% fitted
  cox <- "null"
  zph <- "null"
  optimum <- "null"
  if (length(fitted) > 1) {
    y <- if (is.null(c$entry)) Surv(c$time[used], c$status[used]) else
      Surv(c$entry[used], c$time[used], c$status[used])
    g <- factor(c$group[used], levels = fitted)
    converged <- TRUE
    control <- coxph.control(iter.max = c$iter_max)
    fit <- tryCatch(withCallingHandlers(
      coxph(y ~ g, ties = "efron", control = control),
      warning = function(w) {
        if (grepl("Ran out of iterations", conditionMessage(w))) converged <<- FALSE
        invokeRestart("muffleWarning")
      }), error = function(e) conditionMessage(e))
    if (is.character(fit)) {
      cox <- obj(error = sprintf("\"%s\"", trimws(fit)))
    } else {
      cox <- obj(coef = vec(unname(fit$coefficients)), se = vec(sqrt(diag(fit$var))),
                 loglik = vec(fit$loglik), iter = num(fit$iter),
                 converged = if (converged) "true" else "false")
    }
    tested <- function(fit) {
      table <- tryCatch(suppressWarnings(cox.zph(fit, transform = "km"))$table,
                        error = function(e) NULL)
      if (is.null(table)) "null" else
        obj(statistic = num(table[nrow(table), "chisq"]), df = num(table[nrow(table), "df"]),
            p = num(table[nrow(table), "p"]))
    }
    if (!is.character(fit)) zph <- tested(fit)
    best <- NULL
    for (init in c(0, seq(-10, 10, by = 2))) {
      ok <- TRUE
      tried <- tryCatch(withCallingHandlers(
        coxph(y ~ g, ties = "efron", init = rep(init, length(fitted) - 1)),
        warning = function(w) {
          if (grepl("Ran out of iterations", conditionMessage(w))) ok <<- FALSE
          invokeRestart("muffleWarning")
        }), error = function(e) NULL)
      if (is.null(tried) || !ok || any(is.na(tried$coefficients))) next
      if (is.null(best) || tried$loglik[2] > best$loglik[2]) best <- tried
    }
    if (!is.null(best)) {
      polish <- coxph.control(eps = 1e-13, toler.chol = 1e-15, iter.max = 100)
      best <- suppressWarnings(coxph(y ~ g, ties = "efron", init = unname(best$coefficients),
                                     control = polish))
      optimum <- obj(coef = vec(unname(best$coefficients)), se = vec(sqrt(diag(best$var))),
                     loglik = num(best$loglik[2]), zph = tested(best))
    }
  }
  fits <- c(fits, obj(
    name = sprintf("\"%s\"", c$name),
    group = vec(c$group), time = vec(c$time), status = vec(c$status),
    entry = if (is.null(c$entry)) "null" else vec(c$entry),
    iter_max = num(c$iter_max), fitted = vec(fitted), cox = cox, zph = zph, optimum = optimum
  ))
}

realise <- function(groups, mask) {
  pairs <- which(outer(seq_len(groups), seq_len(groups), "!="), arr.ind = TRUE)
  pairs <- pairs[order(pairs[, 1], pairs[, 2]), , drop = FALSE]
  group <- integer(); time <- numeric(); status <- integer(); entry <- numeric()
  k <- 0
  for (g in seq_len(groups)) {
    k <- k + 1
    group <- c(group, g); entry <- c(entry, k - 0.5); time <- c(time, k); status <- c(status, 1)
  }
  for (edge in seq_len(nrow(pairs))) {
    if (bitwAnd(mask, bitwShiftL(1L, edge - 1L)) == 0) next
    k <- k + 1
    h <- pairs[edge, 1]; e <- pairs[edge, 2]
    group <- c(group, e, h); entry <- c(entry, k - 0.5, k - 0.5)
    time <- c(time, k, k + 0.25); status <- c(status, 1, 0)
  }
  list(group = group, time = time, status = status, entry = entry)
}

edges <- character()
control <- coxph.control(iter.max = 30, eps = 1e-11)
for (groups in 2:4) {
  for (mask in 0:(2^(groups * (groups - 1)) - 1)) {
    d <- realise(groups, mask)
    y <- Surv(d$entry, d$time, d$status)
    g <- factor(d$group, levels = seq_len(groups))
    fitted <- function(init) tryCatch({
      fit <- withCallingHandlers(coxph(y ~ g, ties = "efron", control = control, init = init),
                                 warning = function(w) invokeRestart("muffleWarning"))
      unname(fit$coefficients)
    }, error = function(e) NULL)
    zero <- fitted(rep(0, groups - 1))
    moved <- fitted(0.7 * seq_len(groups - 1))
    edges <- c(edges, obj(groups = num(groups), mask = num(mask),
                          zero = if (is.null(zero)) "null" else vec(zero, 6),
                          moved = if (is.null(moved)) "null" else vec(moved, 6)))
  }
}

cat(sprintf("{\"r\": \"%s\", \"survival\": \"%s\",\n\"fits\": [%s],\n\"edges\": [%s]}\n",
            R.version.string, as.character(packageVersion("survival")),
            paste(fits, collapse = ",\n  "), paste(edges, collapse = ",\n  ")))
