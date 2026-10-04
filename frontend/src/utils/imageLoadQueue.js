const MAX_CONCURRENT = 4;
let active = 0;
const queue = [];

const runNext = () => {
  if (active >= MAX_CONCURRENT || queue.length === 0) return;
  const next = queue.shift();
  next();
};

export const enqueueImageLoad = (loadFn) =>
  new Promise((resolve, reject) => {
    const task = () => {
      active += 1;
      loadFn()
        .then(resolve, reject)
        .finally(() => {
          active -= 1;
          runNext();
        });
    };

    if (active < MAX_CONCURRENT) {
      task();
    } else {
      queue.push(task);
    }
  });
