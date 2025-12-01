const { helperFunction } = require('../src/utils');

describe('Utils', () => {
  it('should add two numbers', () => {
    expect(helperFunction(1, 2)).toBe(3);
  });
});
